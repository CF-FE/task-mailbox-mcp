"""Durable transport records only; this module never executes a task."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
REPORTED_STATUSES = {"REPORTED_COMPLETED", "REPORTED_FAILED", "UNKNOWN"}
MAX_SQLITE_INTEGER = 2**63 - 1
SYSTEM_EVENT_PREFIX = "submitted:"


def _identifier(value, label):
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must contain 1..128 ASCII letters, digits, or ._:-")


def _nonempty_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _client_event_id(value):
    _identifier(value, "event_id")
    if value.startswith(SYSTEM_EVENT_PREFIX):
        raise ValueError("event_id prefix submitted: is reserved for system submission events")


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("value must be finite JSON data with sortable string keys") from error


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Store:
    """One local SQLite file; each operation owns a separate connection."""

    def __init__(self, db_path, max_payload_bytes=1048576,
                 max_database_bytes=256 * 1024 * 1024, max_jobs=10000):
        if type(max_payload_bytes) is not int or max_payload_bytes < 1:
            raise ValueError("max_payload_bytes must be a positive integer")
        if (type(max_database_bytes) is not int
                or not 65536 <= max_database_bytes <= MAX_SQLITE_INTEGER):
            raise ValueError("max_database_bytes must be an integer of at least 65536 bytes")
        if type(max_jobs) is not int or not 1 <= max_jobs <= MAX_SQLITE_INTEGER:
            raise ValueError("max_jobs must be a positive SQLite integer")
        if str(db_path) == ":memory:":
            raise ValueError("a persistent local database path is required")
        self.db_path = Path(db_path).expanduser()
        self.max_payload_bytes = max_payload_bytes
        self.max_database_bytes = max_database_bytes
        self.max_jobs = max_jobs
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PERSISTED',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    latest_result_version INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    event_type TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_hash TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, version)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_recipient ON jobs(recipient)")
            connection.execute("CREATE INDEX IF NOT EXISTS events_job ON events(job_id, cursor)")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            max_pages = self.max_database_bytes // page_size
            if page_count > max_pages:
                raise ValueError("existing database exceeds max_database_bytes; no data was removed")
            actual_max = connection.execute(f"PRAGMA max_page_count = {max_pages}").fetchone()[0]
            if actual_max > max_pages:
                raise ValueError("database page limit exceeds max_database_bytes")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _content(self, value, label):
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be a JSON object")
        content = _json(value)
        try:
            size = len(content.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError(f"{label} must contain valid UTF-8 text") from error
        if size > self.max_payload_bytes:
            raise ValueError(f"{label} exceeds {self.max_payload_bytes} bytes")
        return content

    @staticmethod
    def _job(connection, job_id):
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return {
            "job_id": row["job_id"],
            "payload_hash": row["payload_hash"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
            "status": row["status"],
            "cancel_requested": bool(row["cancel_requested"]),
            "latest_result_version": row["latest_result_version"],
        }

    @staticmethod
    def _event(row):
        return {
            "cursor": row["cursor"], "event_id": row["event_id"],
            "job_id": row["job_id"], "event_type": row["event_type"],
            "created_at": row["created_at"], "data": json.loads(row["data_json"]),
        }

    def _previous_event(self, connection, event_id, job_id, event_type, request_hash):
        row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        if (row["job_id"], row["event_type"], row["request_hash"]) != (
                job_id, event_type, request_hash):
            raise ValueError("event_id already refers to a different request")
        return self._event(row)

    def _append_event(self, connection, event_id, job_id, event_type, request_hash, data):
        created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        cursor = connection.execute(
            "INSERT INTO events(event_id, job_id, event_type, request_hash, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, job_id, event_type, request_hash, _json(data), created_at),
        ).lastrowid
        return self._event(connection.execute(
            "SELECT * FROM events WHERE cursor = ?", (cursor,)).fetchone())

    def submit_job(self, job_id: str, payload: dict, payload_hash: str) -> dict:
        _identifier(job_id, "job_id")
        content = self._content(payload, "payload")
        normalized = json.loads(content)
        if type(normalized.get("schema_version")) is not int or normalized["schema_version"] != 1:
            raise ValueError("schema_version must be 1")
        for label in ("origin_task_id", "sender", "recipient", "report_route"):
            _nonempty_text(normalized.get(label), label)
        if not isinstance(normalized.get("target"), dict) or not normalized["target"]:
            raise ValueError("target must be a nonempty JSON object")
        if not isinstance(normalized.get("content"), str):
            raise ValueError("content must be a string")
        if not isinstance(payload_hash, str) or SHA256.fullmatch(payload_hash) is None:
            raise ValueError("payload_hash must be a lowercase SHA-256 hex digest")
        if _hash(content) != payload_hash:
            raise ValueError("payload_hash does not match canonical payload JSON")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is not None:
                if row["payload_hash"] != payload_hash or row["payload_json"] != content:
                    raise ValueError("job_id already refers to a different payload")
                return self._job(connection, job_id)
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if job_count >= self.max_jobs:
                raise ValueError("max_jobs reached; existing jobs remain available for reconciliation")
            created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            connection.execute(
                "INSERT INTO jobs(job_id, payload_hash, payload_json, recipient, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, payload_hash, content, normalized["recipient"], created_at),
            )
            self._append_event(
                connection, SYSTEM_EVENT_PREFIX + _hash(job_id), job_id, "JOB_PERSISTED",
                _hash(_json({"job_id": job_id, "payload_hash": payload_hash})),
                {"payload_hash": payload_hash, "status": "PERSISTED"},
            )
            return self._job(connection, job_id)

    def stats(self) -> dict:
        """Return usage metadata without task content, identities, or filesystem paths."""
        with self._transaction() as connection:
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            return {
                "max_payload_bytes": self.max_payload_bytes,
                "database_limit_bytes": self.max_database_bytes,
                "max_jobs": self.max_jobs,
                "page_count": page_count,
                "page_size": page_size,
                "allocated_bytes": page_count * page_size,
                "jobs": connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
                "results": connection.execute("SELECT COUNT(*) FROM results").fetchone()[0],
                "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            }

    def get_job(self, job_id: str) -> dict:
        _identifier(job_id, "job_id")
        with self._transaction() as connection:
            return self._job(connection, job_id)

    def list_events(self, cursor: int = 0, limit: int = 50,
                    recipient: str | None = None) -> dict:
        if type(cursor) is not int or not 0 <= cursor <= MAX_SQLITE_INTEGER:
            raise ValueError("cursor must be a nonnegative SQLite integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 through 1000")
        if recipient is not None:
            _nonempty_text(recipient, "recipient")
        with self._transaction() as connection:
            query = "SELECT events.* FROM events JOIN jobs USING(job_id) WHERE cursor > ?"
            parameters = [cursor]
            if recipient is not None:
                query += " AND jobs.recipient = ?"
                parameters.append(recipient)
            query += " ORDER BY cursor LIMIT ?"
            parameters.append(limit + 1)
            rows = connection.execute(query, parameters).fetchall()
            events = [self._event(row) for row in rows[:limit]]
            return {
                "events": events,
                "next_cursor": events[-1]["cursor"] if events else cursor,
                "has_more": len(rows) > limit,
            }

    @staticmethod
    def _result(connection, job_id, version=None):
        if version is None:
            row = connection.execute(
                "SELECT * FROM results WHERE job_id = ? ORDER BY version DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM results WHERE job_id = ? AND version = ?", (job_id, version),
            ).fetchone()
        if row is None:
            raise KeyError((job_id, version))
        return {
            "job_id": row["job_id"], "version": row["version"], "status": row["status"],
            "result": json.loads(row["result_json"]), "result_hash": row["result_hash"],
            "event_id": row["event_id"], "created_at": row["created_at"],
        }

    def publish_result(self, job_id: str, event_id: str, status: str, result: dict) -> dict:
        _identifier(job_id, "job_id")
        _client_event_id(event_id)
        if not isinstance(status, str) or status not in REPORTED_STATUSES:
            raise ValueError("status must be REPORTED_COMPLETED, REPORTED_FAILED, or UNKNOWN")
        content = self._content(result, "result")
        result_hash = _hash(content)
        request_hash = _hash(_json({"job_id": job_id, "status": status,
                                    "result_hash": result_hash}))
        with self._transaction() as connection:
            job = self._job(connection, job_id)
            previous = self._previous_event(
                connection, event_id, job_id, "RESULT_REPORTED", request_hash)
            if previous is not None:
                return self._result(connection, job_id, previous["data"]["version"])
            version = job["latest_result_version"] + 1
            event = self._append_event(
                connection, event_id, job_id, "RESULT_REPORTED", request_hash,
                {"version": version, "status": status, "result_hash": result_hash},
            )
            connection.execute(
                "INSERT INTO results(job_id, version, status, result_json, result_hash, event_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, version, status, content, result_hash, event_id, event["created_at"]),
            )
            connection.execute(
                "UPDATE jobs SET status = ?, latest_result_version = ? WHERE job_id = ?",
                (status, version, job_id),
            )
            return self._result(connection, job_id, version)

    def get_result(self, job_id: str, version: int | None = None) -> dict:
        _identifier(job_id, "job_id")
        if version is not None and (
                type(version) is not int or not 1 <= version <= MAX_SQLITE_INTEGER):
            raise ValueError("version must be a positive SQLite integer")
        with self._transaction() as connection:
            return self._result(connection, job_id, version)

    def request_cancel(self, job_id: str, event_id: str) -> dict:
        _identifier(job_id, "job_id")
        _client_event_id(event_id)
        request_hash = _hash(_json({"job_id": job_id, "event_type": "CANCEL_REQUESTED"}))
        with self._transaction() as connection:
            self._job(connection, job_id)
            previous = self._previous_event(
                connection, event_id, job_id, "CANCEL_REQUESTED", request_hash)
            if previous is not None:
                return previous
            connection.execute("UPDATE jobs SET cancel_requested = 1 WHERE job_id = ?", (job_id,))
            return self._append_event(
                connection, event_id, job_id, "CANCEL_REQUESTED", request_hash, {})

    def ack_result_read(self, job_id: str, event_id: str, version: int, result_hash: str) -> dict:
        _identifier(job_id, "job_id")
        _client_event_id(event_id)
        if type(version) is not int or not 1 <= version <= MAX_SQLITE_INTEGER:
            raise ValueError("version must be a positive SQLite integer")
        if not isinstance(result_hash, str) or SHA256.fullmatch(result_hash) is None:
            raise ValueError("result_hash must be a lowercase SHA-256 hex digest")
        data = {"version": version, "result_hash": result_hash}
        request_hash = _hash(_json({"job_id": job_id, **data}))
        with self._transaction() as connection:
            result = self._result(connection, job_id, version)
            if result["result_hash"] != result_hash:
                raise ValueError("result_hash does not match the requested result version")
            previous = self._previous_event(
                connection, event_id, job_id, "RESULT_READ_ACKNOWLEDGED", request_hash)
            if previous is not None:
                return previous
            return self._append_event(
                connection, event_id, job_id, "RESULT_READ_ACKNOWLEDGED", request_hash, data)
