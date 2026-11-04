"""Authenticated local port owned by the existing collector (no network client)."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import hmac
import json
import os
import re
from pathlib import Path
import secrets
import socket
import sqlite3
import stat
import threading

if __package__:
    from .bridge_store import DIGEST, ID, MAX_INT, _hash, _json
else:
    from bridge_store import DIGEST, ID, MAX_INT, _hash, _json


OPS = ("verified_job", "lookup_report", "enqueue_result")
ARGS = {"probe": set(), "verified_job": {"job_id"}, "lookup_report": {"job_id", "event_id"},
        "enqueue_result": {"project_id", "job_id", "payload_hash", "event_id", "status", "result"}}
FRAME = 2 * 1024 * 1024
POLICY_BYTES = 256 * 1024
GOAL_KIND = "DELEGATE_GOAL"
GOAL_BUDGET = {"dispatch_attempts": 1, "production_writes": 0, "audio_generations": 0}
GOAL_ACTIONS = {"coordinate_existing_owner", "verify_existing_evidence", "plan_independent_review", "report_progress"}
GOAL_FIELDS = {"objective", "work_key", "acceptance_criteria", "evidence_refs", "related_request_ids",
               "scope_refs", "allowed_actions", "next_check_at", "review_requirement"}
GOAL_REQUEST_FIELDS = {"schema_version", "request_id", "idempotency_key", "kind", "expected_generation",
                       "deadline", "authorization_ref", "evidence_refs", "budget", "goal"}


class PortError(ValueError):
    pass


def check(ok, code="INVALID_REQUEST"):
    if not ok:
        raise PortError(code)


def token(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def goal_id(value):
    return token(value) and value[0].isalnum()


def digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def short_text(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and value.strip() == value and value.isprintable()


def goal_text(value, maximum=2048):
    check(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and "\x00" not in value, "CONFLICT")


def goal_strings(value, required=False):
    check(isinstance(value, list) and len(value) <= 64 and (bool(value) or not required), "CONFLICT")
    for item in value:
        goal_text(item)


def goal_time(value):
    goal_text(value, 64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        check(parsed.tzinfo is not None, "CONFLICT")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise PortError("CONFLICT") from None


def check_goal_request(body, task_ref):
    """Validate the external goal contract locally; never import the coordinator runtime."""
    check(set(body) == GOAL_REQUEST_FIELDS and body["kind"] == GOAL_KIND
          and goal_id(body["request_id"]) and goal_id(body["idempotency_key"]) and type(body["expected_generation"]) is int
          and 0 <= body["expected_generation"] < MAX_INT, "CONFLICT")
    goal_text(body["authorization_ref"])
    goal_strings(body["evidence_refs"])
    check(len(body["evidence_refs"]) <= 62, "CONFLICT")
    goal = body["goal"]
    check(isinstance(goal, dict) and set(goal) == GOAL_FIELDS, "CONFLICT")
    goal_text(goal["objective"], 32768)
    for name in ("acceptance_criteria", "evidence_refs", "scope_refs", "allowed_actions"):
        goal_strings(goal[name], required=True)
        check(len(set(goal[name])) == len(goal[name]), "CONFLICT")
    check(all(short_text(ref) for ref in goal["scope_refs"]), "CONFLICT")
    goal_strings(goal["related_request_ids"])
    check(all(goal_id(value) for value in goal["related_request_ids"])
          and set(goal["allowed_actions"]) <= GOAL_ACTIONS
          and goal["review_requirement"] in ("none", "independent_listening"), "CONFLICT")
    check(goal["work_key"] == _hash(_json({"task_ref": task_ref, "scope_refs": sorted(goal["scope_refs"])}))
          and goal_time(goal["next_check_at"]) <= goal_time(body["deadline"]), "CONFLICT")


def check_goal_result(core, body, grant):
    """A signed delivery is a report, not business acceptance or an audio assessment."""
    common = {"schema_version", "kind", "project_id", "request_id", "job_id", "payload_hash", "task_ref",
              "source_principal", "source_evidence_ref"}
    check(type(core.get("schema_version")) is int and core["schema_version"] == 1, "CONFLICT")
    goal_text(core.get("source_principal"))
    goal_text(core.get("source_evidence_ref"))
    if core.get("kind") == "COORDINATOR_WAIT_REPORT":
        check(set(core) == common | {"waiting", "original_kind", "goal_state", "last_seq"}
              and isinstance(core["waiting"], dict) and core["original_kind"] == GOAL_KIND
              and core["goal_state"] in ("ADMITTED", "ACCEPTED", "IN_PROGRESS", "BLOCKED")
              and type(core["last_seq"]) is int and 0 <= core["last_seq"] <= MAX_INT
              and core["source_principal"] == grant["coordinator_id"], "CONFLICT")
        return  # This is a coordinator state snapshot, never a new owner completion report.
    goal = body["goal"]
    check(set(core) == common | {"work_key", "action_id", "generation", "goal_state", "report"}
          and core.get("kind") == "GOAL_UPDATE" and core["work_key"] == goal["work_key"]
          and goal_id(core["action_id"]) and type(core["generation"]) is int
          and core["generation"] == body["expected_generation"], "CONFLICT")
    report = core["report"]
    base = {"seq", "phase", "summary", "observed_at", "evidence_refs", "next_owner"}
    check(isinstance(report, dict) and base <= report.keys() and type(report["seq"]) is int
          and 1 <= report["seq"] <= MAX_INT and report["phase"] in ("ACCEPTED", "IN_PROGRESS", "BLOCKED", "COMPLETED")
          and core["goal_state"] == report["phase"], "CONFLICT")
    goal_text(report["summary"], 8192)
    goal_text(report["next_owner"])
    goal_strings(report["evidence_refs"], required=True)
    observed = goal_time(report["observed_at"])
    check(observed <= datetime.now(timezone.utc), "CONFLICT")
    if report["phase"] != "COMPLETED":
        check(set(report) == base | {"next_check_at"}
              and observed < goal_time(report["next_check_at"]) <= goal_time(body["deadline"]), "CONFLICT")
        return
    expected = base | {"terminal_condition", "criteria_evidence"}
    if goal["review_requirement"] == "independent_listening":
        expected.add("independent_review")
    check(set(report) == expected, "CONFLICT")
    goal_text(report["terminal_condition"], 8192)
    checks = report["criteria_evidence"]
    check(isinstance(checks, list) and len(checks) == len(goal["acceptance_criteria"]), "CONFLICT")
    covered = []
    for item in checks:
        check(isinstance(item, dict) and set(item) == {"criterion", "evidence_refs"}, "CONFLICT")
        goal_text(item["criterion"])
        goal_strings(item["evidence_refs"], required=True)
        covered.append(item["criterion"])
    check(len(set(covered)) == len(covered) and set(covered) == set(goal["acceptance_criteria"]), "CONFLICT")
    if "independent_review" in expected:
        review = report["independent_review"]
        check(isinstance(review, dict) and set(review) == {"state", "reviewer", "producer", "scope_refs", "evidence_refs", "method"}
              and review["state"] == "VERIFIED" and review["method"] in ("human_listening", "audio_perception"), "CONFLICT")
        check(short_text(review["reviewer"]) and short_text(review["producer"]), "CONFLICT")
        goal_strings(review["scope_refs"], required=True)
        goal_strings(review["evidence_refs"], required=True)
        check(review["reviewer"] != review["producer"] and len(review["scope_refs"]) == len(goal["scope_refs"])
              and set(review["scope_refs"]) == set(goal["scope_refs"]), "CONFLICT")


def private_parent(path):
    check(path.is_absolute() and path.parent.resolve(strict=True) == path.parent, "UNAVAILABLE")
    info = path.parent.stat()
    check(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, "UNAVAILABLE")
    for ancestor in path.parent.parents:
        info = ancestor.stat()
        check(info.st_uid in (0, os.geteuid()) and
              (not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)), "UNAVAILABLE")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result)
        result[key] = value
    return result


def transport_deadline(probe):
    value = probe.get("expires_at")
    check(isinstance(value, str) and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", value), "UNAVAILABLE")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise PortError("UNAVAILABLE") from None


def load_policy(path):
    private_parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        check(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
              stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= POLICY_BYTES, "UNAVAILABLE")
        raw = stream.read(POLICY_BYTES + 1)
    check(len(raw) <= POLICY_BYTES, "UNAVAILABLE")
    cfg = json.loads(raw, object_pairs_hook=unique_object)
    check(isinstance(cfg, dict) and set(cfg) == {"v", "socket_path", "keys", "grants"}
          and type(cfg["v"]) is int and cfg["v"] == 1 and isinstance(cfg["socket_path"], str), "UNAVAILABLE")
    keys, grants = cfg["keys"], cfg["grants"]
    check(isinstance(keys, dict) and 1 <= len(keys) <= 32 and all(token(k) and digest(v) for k, v in keys.items())
          and len(set(keys.values())) == len(keys) and isinstance(grants, list) and len(grants) <= 256, "UNAVAILABLE")
    records = {}
    for g in grants:
        required = {"key_id", "actions", "project_id", "coordinator_id", "recipient",
                    "report_route", "job_id", "payload_hash", "origin", "origin_ref", "task"}
        check(isinstance(g, dict) and set(g) in (required, required | {"transport_probe"}, required | {"request_kind"}), "UNAVAILABLE")
        if "request_kind" in g:
            check(g["request_kind"] in ("QUERY_BLOCKER", GOAL_KIND), "UNAVAILABLE")
        if "transport_probe" in g:
            probe = g["transport_probe"]
            check(isinstance(probe, dict) and set(probe) == {"nonce", "expires_at"}
                  and isinstance(probe["nonce"], str)
                  and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", probe["nonce"], re.ASCII), "UNAVAILABLE")
            transport_deadline(probe)
        check(all(token(g[k]) for k in ("key_id", "project_id", "coordinator_id", "job_id"))
              and g["key_id"] in keys and not g["key_id"].startswith("probe-") and digest(g["payload_hash"])
              and isinstance(g["actions"], list) and bool(g["actions"]) and all(a in OPS for a in g["actions"])
              and all(short_text(g[k]) for k in ("recipient", "report_route", "origin_ref")), "UNAVAILABLE")
        origin, task = g["origin"], g["task"]
        check(isinstance(origin, dict) and set(origin) == {"channel", "subject"} and all(short_text(v) for v in origin.values())
              and isinstance(task, dict) and set(task) == {"repository", "project_ref", "item_id", "task_ref", "evidence_ref"}
              and type(task["item_id"]) is int and 0 < task["item_id"] <= MAX_INT and
              all(short_text(v) for k, v in task.items() if k != "item_id") and g["job_id"] not in records, "UNAVAILABLE")
        records[g["job_id"]] = g
    return Path(cfg["socket_path"]), keys, records


def proof_digest(grant, *, explicit_query=False):
    proof = {k: v for k, v in grant.items() if k not in ("key_id", "actions")}
    if proof.get("request_kind", "QUERY_BLOCKER") == "QUERY_BLOCKER" and "transport_probe" not in proof:
        if explicit_query:
            proof["request_kind"] = "QUERY_BLOCKER"
        else:
            proof.pop("request_kind", None)
    return _hash(_json(proof))


def fits(job, g):
    p = job.get("payload")
    if not isinstance(p, dict) or job.get("payload_hash") != g["payload_hash"] or job.get("job_id") != g["job_id"]:
        return False
    t, task, body = p.get("tracking"), g["task"], p.get("coordinator_request")
    if "transport_probe" in g:
        required = {"schema_version", "origin_task_id", "sender", "recipient", "report_route", "target", "content"}
        if (set(p) not in (required, required | {"tracking"})
                or type(p.get("schema_version")) is not int or p["schema_version"] != 1
                or p.get("sender") != "cloud-dot" or p.get("recipient") != g["recipient"]
                or p.get("report_route") != g["report_route"]
                or p.get("target") != {"service": "transport-probe", "operation": "sha256"}
                or p.get("content") != g["transport_probe"]["nonce"]):
            return False
        task_ref = task["task_ref"]
        refs = {task_ref}
        prefix = "https://github.com/" + task["repository"] + "/issues/"
        if task_ref.startswith(prefix) and task_ref[len(prefix):].isdigit():
            refs.add("github:" + task["repository"] + "#" + task_ref[len(prefix):])
        if p.get("origin_task_id") not in refs:
            return False
        return ("tracking" not in p or (isinstance(t, dict)
                and t.get("repository") == task["repository"] and t.get("project_url") == task["project_ref"]
                and type(t.get("item_id")) is int and t["item_id"] == task["item_id"]
                and ((t.get("issue_url") == task_ref and "draft_id" not in t)
                     or (t.get("draft_id") == task_ref and "issue_url" not in t and task_ref.startswith("DI_")))))
    if not isinstance(t, dict) or not isinstance(body, dict):
        return False
    task_ref = task["task_ref"]
    origin_refs = {task_ref}
    if t.get("issue_url") == task_ref:
        num = task_ref.rsplit("/", 1)[-1]
        if task_ref != "https://github.com/" + task["repository"] + "/issues/" + num or not num.isdigit():
            return False
        origin_refs.add("github:" + task["repository"] + "#" + num)
    elif not (t.get("draft_id") == task_ref and task_ref.startswith("DI_")):
        return False
    budget = body.get("budget")
    kind = g.get("request_kind", "QUERY_BLOCKER")
    if kind == GOAL_KIND:
        if not goal_id(g["job_id"]) or not goal_id(g["project_id"]):
            return False
        try:
            check_goal_request(body, task_ref)
            check(_hash(_json(p)) == g["payload_hash"], "CONFLICT")
        except (ValueError, KeyError, TypeError, RecursionError, OverflowError):
            return False
    return (p.get("target") == {"service": "project-coordinator", "project_id": g["project_id"]}
            and p.get("recipient") == g["recipient"] and p.get("report_route") == g["report_route"]
            and t.get("repository") == task["repository"] and t.get("project_url") == task["project_ref"]
            and type(t.get("item_id")) is int and t["item_id"] == task["item_id"]
            and p.get("origin_task_id") in origin_refs and body.get("kind") == kind
            and type(body.get("schema_version")) is int and body["schema_version"] == 1 and token(body.get("request_id"))
            and isinstance(budget, dict) and all(type(v) is int for v in budget.values())
            and budget == (GOAL_BUDGET if kind == GOAL_KIND else
                           {"contact_attempts": 1, "production_writes": 0, "audio_generations": 0}))


class CoordinatorPort:
    def __init__(self, policy, store):
        self.policy, self.store = Path(policy), store
        self.socket_path, _, _ = load_policy(self.policy)
        private_parent(self.socket_path)
        self.requests = set()
        self.lock = threading.Lock()

    def config(self):
        path, keys, grants = load_policy(self.policy)
        check(path == self.socket_path, "UNAVAILABLE")
        return keys, grants

    def capture_proof(self, job):
        try:
            g = self.config()[1].get(job.get("job_id"))
            if g and "transport_probe" in g and (job.get("cancel_requested") is not False or
                    datetime.now(timezone.utc) >= transport_deadline(g["transport_probe"])):
                return None
            return proof_digest(g) if g and fits(job, g) else None
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            return None  # A broken/revoked local grant never authorizes retaining a body.

    def next_missing(self):
        try:
            grants = self.config()[1]
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            return None
        with self.lock:
            candidates = self.requests
            self.requests = set()
        # Each authenticated read queues at most one reconciliation. No polling
        # of supplied/unknown identifiers and no continual retries on a bad proof.
        available = [k for k in sorted(candidates) if k in grants]
        job = self.store.known_uncaptured([(k, grants[k]["payload_hash"]) for k in available])
        if job:
            with self.lock:
                self.requests.update(k for k in available if k > job)
        return job

    def dispatch(self, frame, challenge):
        check(isinstance(frame, dict) and type(frame.get("v")) is int and frame["v"] == 1)
        auth = frame.pop("auth", None)
        check(isinstance(auth, dict) and set(auth) == {"key_id", "mac"} and token(auth["key_id"]) and digest(auth["mac"]), "ACCESS_DENIED")
        keys, grants = self.config()
        key = keys.get(auth["key_id"])
        check(key is not None, "ACCESS_DENIED")
        packed = b"MBRIDGE/1\0" + challenge.encode("ascii") + b"\0" + _json(frame, FRAME).encode()
        check(hmac.compare_digest(hmac.digest(bytes.fromhex(key), packed, "sha256").hex(), auth["mac"]), "ACCESS_DENIED")
        try:
            answer = {"v": 1, "ok": True, "value": self.operation(frame, auth["key_id"], grants)}
        except PortError as error:
            answer = {"v": 1, "ok": False, "code": str(error)}
        except (ValueError, TypeError, KeyError, RecursionError):
            answer = {"v": 1, "ok": False, "code": "INVALID_REQUEST"}
        except (OSError, sqlite3.Error):
            answer = {"v": 1, "ok": False, "code": "UNAVAILABLE"}
        reply = b"MBRIDGE/1/REPLY\0" + challenge.encode("ascii") + b"\0" + auth["mac"].encode("ascii") + b"\0" + _json(answer, FRAME - 128).encode()
        answer["server_mac"] = hmac.digest(bytes.fromhex(key), reply, "sha256").hex()
        return answer

    def operation(self, frame, key_id, grants):
        op = frame.get("op")
        check(isinstance(op, str) and op in ARGS and set(frame) == {"v", "op"} | ARGS[op])
        if op == "probe":
            return {"service": "original-bridge", "protocol": 1, "operations": list(OPS)}
        check(token(frame["job_id"]))
        g = grants.get(frame["job_id"])
        check(g and g["key_id"] == key_id and op in g["actions"], "ACCESS_DENIED")
        if op == "verified_job" and "transport_probe" in g:
            check(datetime.now(timezone.utc) < transport_deadline(g["transport_probe"]), "ACCESS_DENIED")
        record = self.store.coordinator_record(g["job_id"], proof_digest(g))
        if record is None and g.get("request_kind", "QUERY_BLOCKER") == "QUERY_BLOCKER" and "transport_probe" not in g:
            # The initial typed-port version stored an explicit QUERY marker. All
            # other original proof fields must still match the authorized grant.
            record = self.store.coordinator_record(g["job_id"], proof_digest(g, explicit_query=True))
        if record is None:
            if op == "verified_job":
                with self.lock:
                    if len(self.requests) < 256:
                        self.requests.add(g["job_id"])
            raise PortError("UNAVAILABLE")
        check(fits(record, g) and _hash(_json(record["payload"])) == g["payload_hash"], "CONFLICT")
        if op == "verified_job":
            if "transport_probe" in g:
                check(record.get("cancel_requested") is False, "ACCESS_DENIED")
            return {k: g[k] for k in ("origin", "origin_ref", "job_id", "payload_hash", "task")} | {"record": record}
        event_id = frame["event_id"]
        if "transport_probe" in g:
            check(event_id == "transport-probe-" + _hash(_json([g["job_id"], g["payload_hash"]])))
        else:
            check(token(event_id) and event_id.startswith("coordinate-") and digest(event_id[11:]))
        if op == "lookup_report":
            report = self.store.coordinator_report(g["job_id"], event_id)
            if report is None:
                return None
        else:
            check(frame["project_id"] == g["project_id"] and frame["payload_hash"] == g["payload_hash"], "ACCESS_DENIED")
            self.check_result(frame["result"], g, record, event_id, frame["status"])
            if "transport_probe" in g and self.store.coordinator_report(g["job_id"], event_id) is None:
                check(record.get("cancel_requested") is False and
                      datetime.now(timezone.utc) < transport_deadline(g["transport_probe"]), "ACCESS_DENIED")
            try:
                report = self.store.queue_report(g["job_id"], event_id, frame["status"], frame["result"])
            except ValueError as error:
                if str(error) == "report event_id conflicts with an existing report":
                    raise PortError("CONFLICT") from error
                raise PortError("UNAVAILABLE") from error
        self.check_result(report["result"], g, record, event_id, report["status"])
        check(report["result_hash"] == _hash(_json(report["result"])), "CONFLICT")
        return {k: report[k] for k in ("job_id", "event_id", "status", "result_hash", "state", "receipt")}

    @staticmethod
    def check_result(wire, grant, record, event_id, status):
        p = record["payload"]
        if "transport_probe" in grant:
            expected = {"schema_version": 1, "type": "TRANSPORT_PROBE_RESULT", "job_id": grant["job_id"],
                        "payload_hash": grant["payload_hash"], "operation": "sha256",
                        "nonce_sha256": _hash(grant["transport_probe"]["nonce"]),
                        "execution_enabled": False, "business_accepted": False}
            if "tracking" in p:
                expected["task_tracking"] = p["tracking"]
            check(status == "REPORTED_COMPLETED" and isinstance(wire, dict)
                  and type(wire.get("schema_version")) is int and wire.get("execution_enabled") is False
                  and wire.get("business_accepted") is False and wire == expected, "CONFLICT")
            _json(wire)
            return
        req = p["coordinator_request"]["request_id"]
        bind = {"project_id": grant["project_id"], "request_id": req, "job_id": grant["job_id"],
                "payload_hash": grant["payload_hash"]}
        check(status == "REPORTED_COMPLETED" and isinstance(wire, dict) and
              set(wire) == set(bind) | {"schema_version","type","core_version","core_result_hash","result","task_tracking"}
              and type(wire["schema_version"]) is int and wire["schema_version"] == 1 and wire["type"] == "PROJECT_COORDINATOR_RESULT"
              and all(wire[k] == v for k, v in bind.items()) and wire["task_tracking"] == p["tracking"], "CONFLICT")
        version, core = wire["core_version"], wire["result"]
        is_goal = grant.get("request_kind", "QUERY_BLOCKER") == GOAL_KIND
        task_ref = grant["task"]["task_ref"] if is_goal else p["origin_task_id"]
        check(type(version) is int and 1 <= version <= MAX_INT and isinstance(core, dict)
              and all(core.get(k) == v for k, v in bind.items()) and core.get("task_ref") == task_ref
              and wire["core_result_hash"] == _hash(_json(core, 256 * 1024))
              and event_id == "coordinate-" + _hash(_json([grant["coordinator_id"], bind["project_id"], req, bind["job_id"], version])),
              "CONFLICT")
        if is_goal:
            check_goal_result(core, p["coordinator_request"], grant)
        else:
            check(core.get("kind") != "GOAL_UPDATE" and "goal_state" not in core and "work_key" not in core, "CONFLICT")
        _json(wire)  # Recheck the complete tracked result's 1 MiB limit before persistence.

    @asynccontextmanager
    async def listening(self):
        path = self.socket_path
        private_parent(path)
        # Explicit bind never unlinks a pre-existing socket, unlike asyncio's path= form.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        clients = set()
        inode = None
        try:
            sock.bind(os.fspath(path))
            owned = path.lstat()
            inode = (owned.st_dev, owned.st_ino)
            path.chmod(0o600)
            sock.listen(4)
            def accept(reader, writer):
                if len(clients) >= 4:
                    writer.close()
                    return
                task = asyncio.create_task(self.exchange(reader, writer))
                clients.add(task)
                task.add_done_callback(clients.discard)
            server = await asyncio.start_unix_server(accept, sock=sock, limit=FRAME, backlog=4)
            async with server:
                yield
        finally:
            sock.close()
            for task in list(clients):
                task.cancel()
            if clients:
                await asyncio.gather(*clients, return_exceptions=True)
            with suppress(FileNotFoundError):
                cur = path.lstat()
                if inode == (cur.st_dev, cur.st_ino) and stat.S_ISSOCK(cur.st_mode):
                    path.unlink()

    async def exchange(self, reader, writer):
        async def one():
            challenge = secrets.token_hex(32)
            writer.write((_json({"v": 1, "challenge": challenge}) + "\n").encode())
            await writer.drain()
            try:
                line = await reader.readuntil(b"\n")
                check(len(line) <= FRAME)
                obj = json.loads(line, object_pairs_hook=unique_object)
                answer = await asyncio.to_thread(self.dispatch, obj, challenge)
            except PortError as error:
                answer = {"v": 1, "ok": False, "code": str(error)}
            except (json.JSONDecodeError, ValueError, TypeError, KeyError, RecursionError, asyncio.LimitOverrunError):
                answer = {"v": 1, "ok": False, "code": "INVALID_REQUEST"}
            except (OSError, sqlite3.Error):
                answer = {"v": 1, "ok": False, "code": "UNAVAILABLE"}
            writer.write((_json(answer, FRAME - 1) + "\n").encode())
            await writer.drain()
        try:
            await asyncio.wait_for(one(), 5)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass  # Lost receipt: a client must look up the same original event.
        finally:
            writer.close()
