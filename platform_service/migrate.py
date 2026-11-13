"""Apply the schema and transfer a frozen SQLite mailbox directly to PostgreSQL."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError

from mailbox_store import Store, _hash, _json
from .database import configured_engine

TABLES = {
    "jobs": ("job_id", "payload_hash", "payload_json", "recipient", "created_at",
             "status", "cancel_requested", "latest_result_version"),
    "events": ("cursor", "event_id", "job_id", "event_type", "request_hash", "data_json", "created_at"),
    "results": ("job_id", "version", "status", "result_json", "result_hash", "event_id", "created_at"),
}
ORDER = {"jobs": "job_id", "events": "cursor", "results": "job_id, version"}


def upgrade(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    config.attributes["engine"] = engine
    command.upgrade(config, "head")


def fingerprint(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(_json(tuple(row)).encode("utf-8") + b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def copy_mailbox(engine, source):
    with closing(sqlite3.connect(Path(source).resolve(strict=True).as_uri() + "?mode=ro",
                                 uri=True)) as sqlite, engine.begin() as pg:
        sqlite.execute("BEGIN")
        if sqlite.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("source has invalid mailbox foreign keys")
        sequence = sqlite.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
        high = max(sequence[0] if sequence else 0,
                   sqlite.execute("SELECT COALESCE(MAX(cursor), 0) FROM events").fetchone()[0])
        existing = pg.exec_driver_sql(
            "SELECT value FROM platform_cursors WHERE stream='mailbox' FOR UPDATE"
        ).scalar_one()
        counts = pg.exec_driver_sql(
            "SELECT (SELECT count(*) FROM jobs), (SELECT count(*) FROM events), "
            "(SELECT count(*) FROM results)"
        ).one()
        verify_only = bool(existing or any(counts))
        if verify_only and existing != high:
            raise ValueError("target cursor diverges from the source")
        report = {}
        for table, fields in TABLES.items():
            names = ", ".join(fields)
            query = f"SELECT {names} FROM {table} ORDER BY {ORDER[table]}"
            cursor = sqlite.execute(query)
            if not verify_only:
                statement = f"INSERT INTO {table} ({names}) VALUES ({', '.join(['%s'] * len(fields))})"
                while rows := cursor.fetchmany(250):
                    if table in ("jobs", "results"):
                        json_index, hash_index = (2, 1) if table == "jobs" else (3, 4)
                        if any(_hash(row[json_index]) != row[hash_index] for row in rows):
                            raise ValueError(f"{table} contains a mismatched stored SHA-256")
                    if table == "jobs":
                        rows = [(*row[:3], row[3].encode("utf-8"), *row[4:]) for row in rows]
                    pg.exec_driver_sql(statement, rows)
                cursor = sqlite.execute(query)
            expected = fingerprint(cursor)
            # SQLite BINARY order compares UTF-8 bytes. Original job IDs are ASCII.
            pg_query = query.replace("ORDER BY job_id", 'ORDER BY job_id COLLATE "C"')
            rows = pg.exec_driver_sql(pg_query)
            if table == "jobs":
                rows = ((*row[:3], bytes(row[3]).decode("utf-8"), *row[4:]) for row in rows)
            actual = fingerprint(rows)
            if actual != expected:
                raise ValueError(f"{table} diverges from the source")
            report[table] = actual
        if not verify_only:
            pg.exec_driver_sql(
                "UPDATE platform_cursors SET value=%s WHERE stream='mailbox'", (high,)
            )
    return {"tables": report, "cursor_high_watermark": high, "verified_existing": verify_only}


def reconcile_to_sqlite(engine, target, max_database_bytes=268435456):
    """Reconcile into the existing SQLite database while both application writers are stopped."""
    path = Path(target).resolve(strict=True)
    # Use the existing store's write transaction and actual page-budget check.
    store = Store(path, max_database_bytes=max_database_bytes)
    report = {}
    with engine.begin() as pg:
        high = pg.exec_driver_sql(
            "SELECT value FROM platform_cursors WHERE stream='mailbox' FOR UPDATE"
        ).scalar_one()
        with store._transaction() as sqlite:
            sequence = sqlite.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
            if sequence and sequence[0] > high:
                raise ValueError("SQLite cursor is ahead of PostgreSQL")
            # Compare all existing records before mutating anything. Only the job's
            # reported status, cancellation, and latest-result pointer can change.
            for table, fields in TABLES.items():
                names = ", ".join(fields)
                keys = {"jobs": "job_id=%s", "events": "cursor=%s",
                        "results": "job_id=%s AND version=%s"}[table]
                for source in sqlite.execute(f"SELECT {names} FROM {table}"):
                    args = tuple(source[:2] if table == "results" else source[:1])
                    row = pg.exec_driver_sql(f"SELECT {names} FROM {table} WHERE {keys}", args).one_or_none()
                    if table == "jobs" and row:
                        expected = (*row[:3], bytes(row[3]).decode("utf-8"), row[4])
                        equal = tuple(source[:5]) == expected
                    else:
                        equal = row is not None and tuple(source) == tuple(row)
                    if not equal:
                        raise ValueError(f"Existing SQLite {table} record diverges")
            for table in reversed(TABLES):
                sqlite.execute(f"DELETE FROM {table}")
            for table, fields in TABLES.items():
                names = ", ".join(fields)
                order = ORDER[table].replace("job_id", 'job_id COLLATE "C"')
                rows = pg.exec_driver_sql(f"SELECT {names} FROM {table} ORDER BY {order}")
                insert = f"INSERT INTO {table} ({names}) VALUES ({', '.join(['?'] * len(fields))})"
                while chunk := rows.fetchmany(250):
                    if table == "jobs":
                        chunk = [(*row[:3], bytes(row[3]).decode("utf-8"), *row[4:]) for row in chunk]
                    sqlite.executemany(insert, chunk)
                actual = fingerprint(sqlite.execute(f"SELECT {names} FROM {table} ORDER BY {ORDER[table]}"))
                pg_rows = pg.exec_driver_sql(f"SELECT {names} FROM {table} ORDER BY {order}")
                if table == "jobs":
                    pg_rows = ((*row[:3], bytes(row[3]).decode("utf-8"), *row[4:]) for row in pg_rows)
                if actual != fingerprint(pg_rows):
                    raise ValueError(f"SQLite verification failed for {table}")
                report[table] = actual
            sqlite.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            sqlite.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('events', ?)", (high,))
            if sqlite.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("SQLite has invalid mailbox foreign keys")
    return {"tables": report, "cursor_high_watermark": high}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("upgrade", "import-mailbox", "reconcile-to-sqlite"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    if args.operation == "import-mailbox" and args.source is None:
        parser.error("--source is required for import-mailbox")
    if args.operation == "reconcile-to-sqlite" and args.target is None:
        parser.error("--target is required for reconcile-to-sqlite")
    engine = None
    try:
        engine = configured_engine()
        if args.operation == "upgrade":
            upgrade(engine)
        elif args.operation == "import-mailbox":
            print(json.dumps(copy_mailbox(engine, args.source)))
        else:
            print(json.dumps(reconcile_to_sqlite(
                engine, args.target, int(os.environ.get("TASK_MAILBOX_MAX_DATABASE_BYTES", "268435456")))))
    except (SQLAlchemyError, sqlite3.Error, OSError, ValueError, KeyError) as error:
        print(f"Migration failed ({type(error).__name__}). Inspect the configured source and target.",
              file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
