"""Private local coordination ledger. It never calls a network or executor."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

if __package__:
    from .tracking import attach_tracking, validate_tracking
else:
    from tracking import attach_tracking, validate_tracking


ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
SAFE_DETAIL = re.compile(r"[A-Za-z0-9 _.:()-]{0,240}\Z", re.ASCII)
STATUSES = {"REPORTED_COMPLETED", "REPORTED_FAILED", "UNKNOWN"}
HOOK_STATES = {"SessionStart": "IDLE", "UserPromptSubmit": "ACTIVE",
               "PreToolUse": "ACTIVE", "PostToolUse": "ACTIVE",
               "Stop": "IDLE", "Interrupt": "INTERRUPTED", "SessionEnd": "CLOSED"}
MAX_INT = 2**63 - 1


def _id(value, label):
    if not isinstance(value, str) or ID.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _number(value, label, minimum=0, maximum=MAX_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {label}")
    return value


def _json(value, maximum=1048576):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        if len(text.encode("utf-8")) > maximum:
            raise ValueError("record exceeds local byte limit")
        return text
    except (TypeError, UnicodeError, OverflowError) as error:
        raise ValueError("record must be finite UTF-8 JSON") from error


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _detail(value):
    if not isinstance(value, str) or SAFE_DETAIL.fullmatch(value) is None:
        raise ValueError("detail must be a short safe code, without URLs or raw error data")
    return value


def _cwd(value):
    if not isinstance(value, str) or not value or "\0" in value or not os.path.isabs(value):
        raise ValueError("cwd must be an explicit absolute path")
    return os.path.normpath(value)


def read_held_events(path, source_id, consumer_id="mac-coordinator", *, cursor=0, limit=25):
    """Read bounded HELD metadata without initializing or mutating a ledger."""
    _id(source_id, "source_id")
    _id(consumer_id, "consumer_id")
    _number(cursor, "cursor")
    _number(limit, "limit", 1, 100)
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("ledger must not be a symlink")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        identity = dict(connection.execute("SELECT key,value FROM meta WHERE key IN ('source_id','consumer_id')"))
        if identity != {"source_id": source_id, "consumer_id": consumer_id}:
            raise ValueError("ledger source and consumer identities do not match")
        rows = connection.execute(
            "SELECT cursor,event_id,body,reason FROM inbox WHERE state='HELD' AND cursor>? ORDER BY cursor LIMIT ?",
            (cursor, limit + 1)).fetchall()
        events = []
        for row in rows[:limit]:
            event = json.loads(row["body"])
            # Ingested creation-time strings are not constrained to timestamps.
            # Expose a timestamp or null, never that unconstrained source value.
            try:
                created = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
                created_at = created.isoformat() if created.tzinfo is not None else None
            except ValueError:
                created_at = None
            events.append({"cursor": row["cursor"], "event_id": row["event_id"],
                "job_id": event["job_id"], "event_type": event["event_type"],
                "bridge_state": "HELD", "reason": row["reason"], "created_at": created_at})
        return {"events": events, "next_cursor": events[-1]["cursor"] if events else cursor,
                "has_more": len(rows) > limit}


class BridgeStore:
    def __init__(self, path, source_id, consumer_id="mac-coordinator",
                 max_database_bytes=33554432, max_pending=500):
        self.source_id = _id(source_id, "source_id")
        self.consumer_id = _id(consumer_id, "consumer_id")
        self.max_database_bytes = _number(max_database_bytes, "database budget", 131072)
        self.max_pending = _number(max_pending, "pending limit", 1)
        if str(path) == ":memory:":
            raise ValueError("a persistent private local path is required")
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("ledger must not be a symlink")
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        with self._transaction() as connection:
            for statement in (
                "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY, cursor INTEGER UNIQUE NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING', reason TEXT NOT NULL DEFAULT '')",
                "CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, status TEXT NOT NULL, cancel_requested INTEGER NOT NULL, latest_result_version INTEGER NOT NULL, created_at TEXT NOT NULL, routing TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(job_id), cwd TEXT NOT NULL, state TEXT NOT NULL, last_event TEXT NOT NULL, last_turn_id TEXT NOT NULL, updated_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS hooks(hook_key TEXT PRIMARY KEY, body TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS reports(event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id), status TEXT NOT NULL, result TEXT NOT NULL, result_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING', receipt TEXT, created_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS health(component TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT NOT NULL, updated_at TEXT NOT NULL)",
            ):
                connection.execute(statement)
            for key, value in (("source_id", source_id), ("consumer_id", consumer_id)):
                row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                if row is not None and row[0] != value:
                    raise ValueError("ledger source and consumer identities cannot be changed")
                connection.execute("INSERT OR IGNORE INTO meta VALUES (?, ?)", (key, value))
            connection.execute("INSERT OR IGNORE INTO meta VALUES ('cursor', '0')")

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=1000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            size = connection.execute("PRAGMA page_size").fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            maximum = self.max_database_bytes // size
            if pages > maximum:
                raise ValueError("existing ledger exceeds configured database budget")
            actual = connection.execute(f"PRAGMA max_page_count={maximum}").fetchone()[0]
            if actual > maximum:
                raise ValueError("ledger cannot apply configured database budget")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _cursor(connection):
        return int(connection.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])

    def cursor(self):
        with self._transaction() as connection:
            return self._cursor(connection)

    def ingest_page(self, page):
        if (not isinstance(page, dict) or not isinstance(page.get("events"), list)
                or type(page.get("has_more")) is not bool):
            raise ValueError("invalid event page")
        events = page["events"]
        if len(events) > 1000:
            raise ValueError("event page exceeds limit")
        next_cursor = _number(page.get("next_cursor"), "next_cursor")
        prepared, last = [], 0
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
                raise ValueError("invalid event")
            cursor = _number(event.get("cursor"), "event cursor", 1)
            if cursor <= last:
                raise ValueError("event cursors must be strictly increasing within a page")
            last = cursor
            _id(event.get("event_id"), "event_id")
            _id(event.get("job_id"), "job_id")
            _id(event.get("event_type"), "event_type")
            if not isinstance(event.get("created_at"), str):
                raise ValueError("event creation time is required")
            prepared.append((event["event_id"], cursor, _json(event, 65536)))
        if (events and next_cursor != last) or (not events and page["has_more"]):
            raise ValueError("page cursor does not match returned events")
        with self._transaction() as connection:
            current = self._cursor(connection)
            if not events and next_cursor > current:
                raise ValueError("empty page cannot advance the local cursor")
            added = duplicates = 0
            pending = connection.execute("SELECT COUNT(*) FROM inbox WHERE state!='HANDLED'").fetchone()[0]
            for event_id, cursor, body in prepared:
                previous = connection.execute("SELECT cursor,body FROM inbox WHERE event_id=?", (event_id,)).fetchone()
                if previous is not None:
                    if previous["cursor"] != cursor or previous["body"] != body:
                        raise ValueError("event_id conflicts with a persisted event")
                    duplicates += 1
                    continue
                if cursor <= current:
                    raise ValueError("new event appeared behind the persisted cursor")
                if pending + added >= self.max_pending:
                    raise ValueError("local pending event limit reached")
                if connection.execute("SELECT 1 FROM inbox WHERE cursor=?", (cursor,)).fetchone():
                    raise ValueError("cursor conflicts with a persisted event")
                connection.execute("INSERT INTO inbox(event_id,cursor,body) VALUES (?,?,?)", (event_id, cursor, body))
                added += 1
            current = max(current, next_cursor)
            connection.execute("UPDATE meta SET value=? WHERE key='cursor'", (str(current),))
            return {"received": added, "duplicates": duplicates, "cursor": current,
                    "has_more": page["has_more"]}

    def pending_events(self, limit=50):
        _number(limit, "limit", 1, 1000)
        with self._transaction() as connection:
            rows = connection.execute("SELECT * FROM inbox WHERE state='PENDING' ORDER BY cursor LIMIT ?", (limit,))
            return [{**json.loads(row["body"]), "bridge_state": row["state"], "reason": row["reason"]} for row in rows]

    def finish_event(self, event_id, state="HANDLED", reason=""):
        _id(event_id, "event_id")
        if state not in ("HANDLED", "HELD"):
            raise ValueError("event state must be HANDLED or HELD")
        _detail(reason)
        with self._transaction() as connection:
            if not connection.execute("SELECT 1 FROM inbox WHERE event_id=?", (event_id,)).fetchone():
                raise KeyError(event_id)
            connection.execute("UPDATE inbox SET state=?,reason=? WHERE event_id=?", (state, reason, event_id))
            return {"event_id": event_id, "bridge_state": state, "reason": reason}

    @staticmethod
    def _job(connection, job_id):
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        routing = json.loads(row["routing"])
        if "tracking" in routing:
            routing.update(tracking_is_caller_supplied=True, tracking_verification="UNVERIFIED")
        return {**routing, "routing_is_caller_supplied": True,
                "job_id": row["job_id"], "payload_hash": row["payload_hash"], "status": row["status"],
                "cancel_requested": bool(row["cancel_requested"]), "latest_result_version": row["latest_result_version"]}

    def record_job(self, record):
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            raise ValueError("job record and payload must be objects")
        job_id = _id(record.get("job_id"), "job_id")
        payload = record["payload"]
        content = _json(payload)
        if record.get("payload_hash") != _hash(content):
            raise ValueError("job payload hash mismatch")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("unsupported job schema")
        for name in ("origin_task_id", "sender", "recipient", "report_route"):
            if not isinstance(payload.get(name), str) or not payload[name].strip():
                raise ValueError("job routing fields must be nonempty strings")
        if not isinstance(payload.get("target"), dict) or not payload["target"] or not isinstance(payload.get("content"), str):
            raise ValueError("job needs an explicit target and text content")
        routing = {key: payload[key] for key in ("origin_task_id", "recipient", "report_route", "target")}
        if "tracking" in payload:
            routing["tracking"] = validate_tracking(payload["tracking"])
        routing = _json(routing, 65536)
        status = record.get("status")
        if not isinstance(status, str) or status not in STATUSES | {"PERSISTED"} or type(record.get("cancel_requested")) is not bool:
            raise ValueError("invalid reported job state")
        version = _number(record.get("latest_result_version"), "result version")
        created_at = record.get("created_at", "")
        if not isinstance(created_at, str) or len(created_at) > 128:
            raise ValueError("invalid job creation time")
        with self._transaction() as connection:
            previous = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if previous is not None:
                if (previous["payload_hash"] != record["payload_hash"] or previous["created_at"] != created_at
                        or previous["routing"] != routing):
                    raise ValueError("immutable job identity changed")
                if (version < previous["latest_result_version"] or (previous["cancel_requested"] and not record["cancel_requested"])
                        or (version == previous["latest_result_version"] and previous["status"] != status)):
                    raise ValueError("job state moved backwards or conflicts with its version")
            connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,cancel_requested=excluded.cancel_requested,latest_result_version=excluded.latest_result_version",
                (job_id, record["payload_hash"], status, int(record["cancel_requested"]), version, created_at, routing))
            return self._job(connection, job_id)

    @classmethod
    def _binding(cls, connection, session_id):
        row = connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None or row["job_id"] is None:
            return None
        return {"session_id": session_id, "job_id": row["job_id"], "cwd": row["cwd"],
                "job": cls._job(connection, row["job_id"])}

    def session_binding(self, session_id):
        _id(session_id, "session_id")
        with self._transaction() as connection:
            return self._binding(connection, session_id)

    def bind_session(self, session_id, job_id, cwd):
        _id(session_id, "session_id")
        _id(job_id, "job_id")
        cwd = _cwd(cwd)
        with self._transaction() as connection:
            if "tracking" not in self._job(connection, job_id):
                raise ValueError("project session binding requires explicit GitHub tracking; reconcile the original task without changing its payload/hash or redispatching work")
            connection.execute("INSERT INTO sessions VALUES (?,?,?,'UNKNOWN','','','') ON CONFLICT(session_id) DO UPDATE SET job_id=excluded.job_id,cwd=excluded.cwd",
                               (session_id, job_id, cwd))
            return self._binding(connection, session_id)

    def record_hook(self, event):
        if not isinstance(event, dict):
            raise ValueError("hook event must be an object")
        name = event.get("hook_event_name")
        if not isinstance(name, str) or name not in HOOK_STATES:
            raise ValueError("unsupported hook event")
        session_id = _id(event.get("session_id"), "session_id")
        cwd = _cwd(event.get("cwd"))
        turn_id = event.get("turn_id")
        if turn_id is None:
            turn_id = ""
        if name not in {"SessionStart", "SessionEnd"} or turn_id:
            _id(turn_id, "turn_id")
        safe = {"session_id": session_id, "turn_id": turn_id, "cwd": cwd, "hook_event_name": name}
        if name in {"PreToolUse", "PostToolUse"}:
            for key in ("tool_use_id", "tool_name"):
                safe[key] = _id(event.get(key), key)
        body = _json(safe, 16384)
        key = _hash(_json([session_id, turn_id, name, safe.get("tool_use_id", "")]))
        with self._transaction() as connection:
            binding = self._binding(connection, session_id)
            if binding and binding["cwd"] != cwd:
                raise ValueError("hook cwd conflicts with the explicit session binding")
            prior = connection.execute("SELECT body FROM hooks WHERE hook_key=?", (key,)).fetchone()
            if prior is not None and prior[0] != body:
                raise ValueError("hook identity conflicts with persisted references")
            duplicate = prior is not None
            lifecycle_observation = name in {"SessionStart", "SessionEnd"} and not turn_id
            if not duplicate or lifecycle_observation:
                if not duplicate:
                    connection.execute("INSERT INTO hooks VALUES (?,?)", (key, body))
                state = HOOK_STATES[name]
                connection.execute("INSERT INTO sessions VALUES (?,NULL,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET cwd=excluded.cwd,state=excluded.state,last_event=excluded.last_event,last_turn_id=excluded.last_turn_id,updated_at=excluded.updated_at",
                                   (session_id, cwd, state, name, turn_id, _now()))
            row = connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            session = {"session_id": session_id, "state": row["state"], "job_id": row["job_id"],
                       "observed_at": row["updated_at"] or None,
                       "payload_hash": binding["job"]["payload_hash"] if binding else None}
            return {"recorded": True, "duplicate": duplicate, "session": session, "binding": binding}

    @staticmethod
    def _report(row):
        return {"job_id": row["job_id"], "event_id": row["event_id"], "status": row["status"],
                "result": json.loads(row["result"]), "result_hash": row["result_hash"], "state": row["state"],
                "receipt": json.loads(row["receipt"]) if row["receipt"] else None, "created_at": row["created_at"]}

    def queue_report(self, job_id, event_id, status, result):
        _id(job_id, "job_id")
        _id(event_id, "event_id")
        if event_id.startswith("submitted:") or not isinstance(status, str) or status not in STATUSES or not isinstance(result, dict):
            raise ValueError("invalid report identity, status, or result")
        with self._transaction() as connection:
            job = self._job(connection, job_id)
            content = _json(attach_tracking(result, job.get("tracking")))
            result_hash = _hash(content)
            previous = connection.execute("SELECT * FROM reports WHERE event_id=?", (event_id,)).fetchone()
            if previous is not None:
                if (previous["job_id"], previous["status"], previous["result"]) != (job_id, status, content):
                    raise ValueError("report event_id conflicts with an existing report")
                return self._report(previous)
            if connection.execute("SELECT COUNT(*) FROM reports WHERE state='PENDING'").fetchone()[0] >= self.max_pending:
                raise ValueError("local pending report limit reached")
            connection.execute("INSERT INTO reports(event_id,job_id,status,result,result_hash,created_at) VALUES (?,?,?,?,?,?)",
                               (event_id, job_id, status, content, result_hash, _now()))
            return self._report(connection.execute("SELECT * FROM reports WHERE event_id=?", (event_id,)).fetchone())

    def pending_reports(self, limit=50):
        _number(limit, "limit", 1, 1000)
        with self._transaction() as connection:
            return [self._report(row) for row in connection.execute("SELECT * FROM reports WHERE state='PENDING' ORDER BY created_at,event_id LIMIT ?", (limit,))]

    def mark_report_sent(self, event_id, receipt):
        _id(event_id, "event_id")
        if not isinstance(receipt, dict):
            raise ValueError("report receipt must be an object")
        _number(receipt.get("version"), "receipt version", 1)
        # Persist only the receipt identity, never an echoed result or unrelated fields.
        safe = {key: receipt.get(key) for key in ("job_id", "event_id", "status", "result_hash", "version")}
        content = _json(safe)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM reports WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            if any(safe[key] != row[key] for key in ("job_id", "event_id", "status", "result_hash")):
                raise ValueError("report receipt does not match the queued report")
            if row["receipt"] is not None and row["receipt"] != content:
                raise ValueError("report receipt conflicts with its saved version")
            connection.execute("UPDATE reports SET state='SENT',receipt=? WHERE event_id=?", (content, event_id))
            return self._report(connection.execute("SELECT * FROM reports WHERE event_id=?", (event_id,)).fetchone())

    def set_health(self, component, state, detail=""):
        _id(component, "component")
        _id(state, "health state")
        _detail(detail)
        with self._transaction() as connection:
            updated_at = _now()
            connection.execute("INSERT INTO health VALUES (?,?,?,?) ON CONFLICT(component) DO UPDATE SET state=excluded.state,detail=excluded.detail,updated_at=excluded.updated_at",
                               (component, state, detail, updated_at))
            return {"component": component, "state": state, "detail": detail, "updated_at": updated_at}

    def snapshot(self):
        with self._transaction() as connection:
            jobs = [self._job(connection, row[0]) for row in connection.execute("SELECT job_id FROM jobs ORDER BY job_id")]
            sessions = []
            for row in connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 100"):
                job = self._job(connection, row["job_id"]) if row["job_id"] else None
                session = {"session_id": row["session_id"], "state": row["state"], "job_id": row["job_id"],
                    "payload_hash": job["payload_hash"] if job else None, "last_event": row["last_event"],
                    "last_turn_id": row["last_turn_id"], "observed_at": row["updated_at"] or None,
                    "updated_at": row["updated_at"] or None}
                if job and "tracking" in job:
                    session.update(tracking=job["tracking"], tracking_is_caller_supplied=True,
                                   tracking_verification="UNVERIFIED")
                sessions.append(session)
            recent_results = []
            cancel = {job["job_id"] for job in jobs if job["cancel_requested"]}
            reported = {job["job_id"]: (job["latest_result_version"], job["status"]) for job in jobs}
            for row in connection.execute("SELECT body,state FROM inbox ORDER BY cursor DESC"):
                event = json.loads(row["body"])
                if event["event_type"] == "CANCEL_REQUESTED":
                    cancel.add(event["job_id"])
                if event["event_type"] == "RESULT_REPORTED":
                    data = event["data"]
                    version = data.get("version")
                    status = data.get("status")
                    valid = type(version) is int and version > 0 and isinstance(status, str) and status in STATUSES
                    if valid and row["state"] == "HANDLED":
                        previous_version, _ = reported.get(event["job_id"], (0, "PERSISTED"))
                        if version > previous_version:
                            reported[event["job_id"]] = (version, status)
                    if len(recent_results) < 50:
                        result_hash = data.get("result_hash")
                        recent_results.append({"job_id": event["job_id"], "event_id": event["event_id"],
                            "version": version if valid else None, "status": status if valid else "UNVERIFIED",
                            "result_hash": result_hash if isinstance(result_hash, str) and DIGEST.fullmatch(result_hash) else None,
                            "bridge_state": row["state"]})
            for row in connection.execute("SELECT job_id,status,receipt FROM reports WHERE state='SENT'"):
                receipt = json.loads(row["receipt"])
                previous_version, _ = reported.get(row["job_id"], (0, "PERSISTED"))
                if receipt["version"] > previous_version:
                    reported[row["job_id"]] = (receipt["version"], row["status"])
            unknown = {job_id for job_id, (_, status) in reported.items() if status == "UNKNOWN"}
            unknown.update(row[0] for row in connection.execute("SELECT DISTINCT job_id FROM reports WHERE status='UNKNOWN' AND state='PENDING'"))
            count = lambda query: connection.execute(query).fetchone()[0]
            return {"source_id": self.source_id, "consumer_id": self.consumer_id, "cursor": self._cursor(connection),
                "counts": {"pending_events": count("SELECT COUNT(*) FROM inbox WHERE state='PENDING'"),
                    "held_events": count("SELECT COUNT(*) FROM inbox WHERE state='HELD'"),
                    "pending_reports": count("SELECT COUNT(*) FROM reports WHERE state='PENDING'"),
                    "jobs": len(jobs), "sessions": count("SELECT COUNT(*) FROM sessions"),
                    "cancel_requested": len(cancel), "unknown": len(unknown)},
                "jobs": jobs, "cancel_requested": sorted(cancel), "unknown": sorted(unknown),
                "recent_results": recent_results, "sessions": sessions,
                "health": [dict(row) for row in connection.execute("SELECT * FROM health ORDER BY component")]}
