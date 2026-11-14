"""Preserve mailbox records and add an independent observation stream."""

from alembic import op

revision = "0001_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE platform_cursors (
            stream text PRIMARY KEY,
            value bigint NOT NULL CHECK (value >= 0)
        )
    """)
    op.execute("INSERT INTO platform_cursors VALUES ('mailbox', 0), ('observations', 0)")
    op.execute("""
        CREATE TABLE jobs (
            job_id text PRIMARY KEY,
            payload_hash text NOT NULL,
            payload_json text NOT NULL,
            recipient bytea NOT NULL,
            created_at text NOT NULL,
            status text NOT NULL DEFAULT 'PERSISTED',
            cancel_requested integer NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
            latest_result_version bigint NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX jobs_recipient ON jobs(recipient)")
    op.execute("""
        CREATE TABLE events (
            cursor bigint PRIMARY KEY,
            event_id text NOT NULL UNIQUE,
            job_id text NOT NULL REFERENCES jobs(job_id),
            event_type text NOT NULL,
            request_hash text NOT NULL,
            data_json text NOT NULL,
            created_at text NOT NULL
        )
    """)
    op.execute("CREATE INDEX events_job ON events(job_id, cursor)")
    op.execute("""
        CREATE TABLE results (
            job_id text NOT NULL REFERENCES jobs(job_id),
            version bigint NOT NULL,
            status text NOT NULL,
            result_json text NOT NULL,
            result_hash text NOT NULL,
            event_id text NOT NULL UNIQUE REFERENCES events(event_id),
            created_at text NOT NULL,
            PRIMARY KEY (job_id, version)
        )
    """)
    op.execute("""
        CREATE TABLE observation_events (
            cursor bigint PRIMARY KEY,
            source_id text NOT NULL,
            event_id text NOT NULL,
            project_id text NOT NULL,
            entity_type text NOT NULL,
            entity_id text NOT NULL,
            task_id text,
            observed_at timestamptz NOT NULL,
            received_at timestamptz NOT NULL,
            request_hash text NOT NULL,
            body jsonb NOT NULL,
            UNIQUE (source_id, event_id)
        )
    """)
    op.execute("CREATE INDEX observation_project_cursor ON observation_events(project_id, cursor)")
    op.execute("CREATE INDEX observation_task_cursor ON observation_events(project_id, task_id, cursor)")
    op.execute("CREATE INDEX observation_source_receipt ON observation_events(project_id, source_id, cursor DESC)")
    op.execute("""
        CREATE TABLE observation_batches (
            source_id text NOT NULL,
            batch_id text NOT NULL,
            request_hash text NOT NULL,
            receipt jsonb NOT NULL,
            projects text[] NOT NULL,
            PRIMARY KEY (source_id, batch_id)
        )
    """)
    op.execute("""
        CREATE TABLE observation_snapshots (
            project_id text NOT NULL,
            entity_type text NOT NULL,
            entity_id text NOT NULL,
            source_id text NOT NULL,
            cursor bigint NOT NULL REFERENCES observation_events(cursor),
            observed_at timestamptz NOT NULL,
            source_revision bigint,
            task_id text,
            PRIMARY KEY (project_id, entity_type, entity_id, source_id)
        )
    """)
    op.execute("CREATE INDEX observation_snapshot_task ON observation_snapshots(project_id, task_id, entity_type)")
    op.execute("""
        CREATE TABLE web_sessions (
            token_hash text PRIMARY KEY,
            github_login text NOT NULL,
            subject text NOT NULL,
            expires_at timestamptz NOT NULL
        )
    """)
    op.execute("CREATE INDEX web_sessions_expiration ON web_sessions(expires_at)")


def downgrade():
    raise RuntimeError("Use the explicit mailbox reconciliation path to return to SQLite")
