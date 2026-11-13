"""PostgreSQL engine and the legacy wire-compatible Store."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from mailbox_store import MAX_SQLITE_INTEGER, Store, _json

REVISION = "0001_platform"


def database_engine(dsn, *, require_tls=False):
    url = make_url(dsn)
    if url.drivername not in ("postgresql", "postgresql+psycopg"):
        raise ValueError("platform database requires PostgreSQL with psycopg")
    if require_tls and (
        url.query.get("sslmode") != "verify-full" or not url.query.get("sslrootcert")
        or not url.host or url.query.get("host") or url.query.get("service")
    ):
        raise ValueError("production PostgreSQL requires a verified TLS hostname and sslrootcert")
    url = url.set(drivername="postgresql+psycopg")
    if require_tls:
        url = url.update_query_dict({"gssencmode": "disable"})
    return create_engine(url,
                         pool_size=4, max_overflow=2, pool_timeout=4, pool_pre_ping=True,
                         hide_parameters=True, connect_args={
                             "connect_timeout": 15,
                             "options": "-c statement_timeout=5000 -c lock_timeout=4500",
                         })


def configured_engine():
    return database_engine(os.environ["TASK_PLATFORM_DATABASE_URL"], require_tls=True)


class LegacyRow:
    def __init__(self, row):
        self.row = row

    def __getitem__(self, key):
        return self.row[key] if isinstance(key, int) else self.row._mapping[key]


class LegacyResult:
    def __init__(self, result):
        self.result = result

    def fetchone(self):
        row = self.result.fetchone()
        return None if row is None else LegacyRow(row)

    def fetchall(self):
        return [LegacyRow(row) for row in self.result.fetchall()]


class LegacyConnection:
    """Translate only the inherited, static Store statements."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO jobs("):
            parameters = (*parameters[:3], parameters[3].encode("utf-8"), parameters[4])
        elif "AND jobs.recipient = ?" in sql:
            parameters = (parameters[0], parameters[1].encode("utf-8"), parameters[2])
        return LegacyResult(self.connection.exec_driver_sql(
            sql.replace("?", "%s"), tuple(parameters)))


class PostgresStore(Store):
    def __init__(self, engine, max_payload_bytes=1048576, capacity_warning_bytes=268435456,
                 max_jobs=10000):
        if max_payload_bytes < 1 or capacity_warning_bytes < 1 or not 1 <= max_jobs <= MAX_SQLITE_INTEGER:
            raise ValueError("invalid mailbox capacity configuration")
        self.engine = engine
        self.transaction_lock = Lock()
        self.max_payload_bytes = max_payload_bytes
        self.capacity_warning_bytes = capacity_warning_bytes
        self.max_jobs = max_jobs

    @contextmanager
    def _transaction(self):
        # Queue callers before checkout. These operations all take the same PG
        # lock, so parallel checkouts only hold idle connections while waiting.
        with self.transaction_lock, self.engine.begin() as connection:
            connection.exec_driver_sql(
                "SELECT value FROM platform_cursors WHERE stream = 'mailbox' FOR UPDATE"
            ).scalar_one()
            yield LegacyConnection(connection)

    def _append_event(self, connection, event_id, job_id, event_type, request_hash, data):
        created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        row = connection.execute("""
            WITH next_cursor AS (
                UPDATE platform_cursors SET value = value + 1
                WHERE stream = 'mailbox' RETURNING value
            )
            INSERT INTO events(cursor, event_id, job_id, event_type, request_hash, data_json, created_at)
            SELECT value, ?, ?, ?, ?, ?, ? FROM next_cursor RETURNING *
        """, (event_id, job_id, event_type, request_hash, _json(data), created_at)).fetchone()
        return self._event(row)

    def stats(self):
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            row = connection.exec_driver_sql("""
                SELECT pg_database_size(current_database()) AS database_size_bytes,
                    (SELECT COUNT(*) FROM jobs) AS jobs,
                    (SELECT COUNT(*) FROM results) AS results,
                    (SELECT COUNT(*) FROM events) AS events,
                    (SELECT COUNT(*) FROM observation_events) AS observation_events,
                    (SELECT COUNT(*) FROM observation_batches) AS observation_batches
            """).mappings().one()
        return {
            **row, "usage_schema_version": 2, "storage_engine": "postgresql",
            "max_payload_bytes": self.max_payload_bytes, "max_jobs": self.max_jobs,
            "capacity_warning_bytes": self.capacity_warning_bytes,
            "capacity_warning": row["database_size_bytes"] >= self.capacity_warning_bytes,
            "sqlite_page_fields_applicable": False,
            "page_count": None, "page_size": None,
            "allocated_bytes": None, "database_limit_bytes": None,
        }
