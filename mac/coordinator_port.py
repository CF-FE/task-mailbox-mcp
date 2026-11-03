"""Authenticated local port owned by the existing collector (no network client)."""

import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import os
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


class PortError(ValueError):
    pass


def check(ok, code="INVALID_REQUEST"):
    if not ok:
        raise PortError(code)


def token(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def short_text(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and value.strip() == value and value.isprintable()


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
        check(isinstance(g, dict) and set(g) == {"key_id", "actions", "project_id", "coordinator_id", "recipient",
              "report_route", "job_id", "payload_hash", "origin", "origin_ref", "task"}, "UNAVAILABLE")
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


def proof_digest(grant):
    # Key rotation and narrowing actions preserve the independently attested origin.
    return _hash(_json({k: v for k, v in grant.items() if k not in ("key_id", "actions")}))


def fits(job, g):
    p = job.get("payload")
    if not isinstance(p, dict) or job.get("payload_hash") != g["payload_hash"] or job.get("job_id") != g["job_id"]:
        return False
    t, task, body = p.get("tracking"), g["task"], p.get("coordinator_request")
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
    return (p.get("target") == {"service": "project-coordinator", "project_id": g["project_id"]}
            and p.get("recipient") == g["recipient"] and p.get("report_route") == g["report_route"]
            and t.get("repository") == task["repository"] and t.get("project_url") == task["project_ref"]
            and type(t.get("item_id")) is int and t["item_id"] == task["item_id"]
            and p.get("origin_task_id") in origin_refs and body.get("kind") == "QUERY_BLOCKER"
            and type(body.get("schema_version")) is int and body["schema_version"] == 1 and token(body.get("request_id"))
            and isinstance(budget, dict) and all(type(v) is int for v in budget.values())
            and budget == {"contact_attempts": 1, "production_writes": 0, "audio_generations": 0})


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
        record = self.store.coordinator_record(g["job_id"], proof_digest(g))
        if record is None:
            if op == "verified_job":
                with self.lock:
                    if len(self.requests) < 256:
                        self.requests.add(g["job_id"])
            raise PortError("UNAVAILABLE")
        check(fits(record, g) and _hash(_json(record["payload"])) == g["payload_hash"], "CONFLICT")
        if op == "verified_job":
            return {k: g[k] for k in ("origin", "origin_ref", "job_id", "payload_hash", "task")} | {"record": record}
        event_id = frame["event_id"]
        check(token(event_id) and event_id.startswith("coordinate-") and digest(event_id[11:]))
        if op == "lookup_report":
            report = self.store.coordinator_report(g["job_id"], event_id)
            if report is None:
                return None
        else:
            check(frame["project_id"] == g["project_id"] and frame["payload_hash"] == g["payload_hash"], "ACCESS_DENIED")
            self.check_result(frame["result"], g, record, event_id, frame["status"])
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
        req = p["coordinator_request"]["request_id"]
        bind = {"project_id": grant["project_id"], "request_id": req, "job_id": grant["job_id"],
                "payload_hash": grant["payload_hash"]}
        check(status == "REPORTED_COMPLETED" and isinstance(wire, dict) and
              set(wire) == set(bind) | {"schema_version","type","core_version","core_result_hash","result","task_tracking"}
              and type(wire["schema_version"]) is int and wire["schema_version"] == 1 and wire["type"] == "PROJECT_COORDINATOR_RESULT"
              and all(wire[k] == v for k, v in bind.items()) and wire["task_tracking"] == p["tracking"], "CONFLICT")
        version, core = wire["core_version"], wire["result"]
        check(type(version) is int and 1 <= version <= MAX_INT and isinstance(core, dict)
              and all(core.get(k) == v for k, v in bind.items()) and core.get("task_ref") == p["origin_task_id"]
              and wire["core_result_hash"] == _hash(_json(core, 256 * 1024))
              and event_id == "coordinate-" + _hash(_json([grant["coordinator_id"], bind["project_id"], req, bind["job_id"], version])),
              "CONFLICT")
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
