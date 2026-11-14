"""Bounded SQLite outbox for independently authenticated observations."""

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

MAX_BATCH_BYTES = 1024 * 1024
MAX_BATCH_EVENTS = 100
BATCH_ID_PLACEHOLDER = "batch:" + "0" * 64
MAX_DATABASE_BYTES = 32 * 1024 * 1024
ID = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z', re.ASCII)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def stable_id(prefix, value):
    return prefix + ':' + hashlib.sha256(canonical(value).encode()).hexdigest()


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def batch_envelope(source_id, batch_id, events):
    return {'schema_version': 1, 'source_id': source_id, 'batch_id': batch_id, 'events': events}


def envelope_bytes(source_id):
    return len(canonical(batch_envelope(source_id, BATCH_ID_PLACEHOLDER, [])).encode())


class ObservationStore:
    def __init__(self, path, *, max_bytes=MAX_DATABASE_BYTES, timeout=0.08):
        if type(max_bytes) is not int or not 32768 <= max_bytes <= MAX_DATABASE_BYTES:
            raise ValueError('invalid observation buffer capacity')
        self.path = Path(path)
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    UNIQUE (source_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL UNIQUE,
                    last_seq INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seen_native_hooks (
                    source_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    PRIMARY KEY (source_id, event_id)
                ) WITHOUT ROWID;
            """)

    def _connect(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'rb') as stream:
            os.fchmod(stream.fileno(), 0o600)
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        try:
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            limit = self.max_bytes // page_size
            if db.execute(f'PRAGMA max_page_count = {limit}').fetchone()[0] != limit:
                raise ValueError('existing observation buffer exceeds its page budget')
        except BaseException:
            db.close()
            raise
        return db

    @contextmanager
    def _write(self):
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def _add(self, db, source_id, event):
        event_id = event['event_id']
        if not valid_id(source_id) or not valid_id(event_id):
            raise ValueError('invalid observation source or event identity')
        body = canonical(event)
        if envelope_bytes(source_id) + len(body.encode()) > MAX_BATCH_BYTES:
            raise ValueError('observation exceeds single event byte budget')
        result = db.execute(
            'SELECT body FROM observations WHERE source_id=? AND event_id=?', (source_id, event_id)
        ).fetchone()
        if result:
            if result[0] != body:
                raise ValueError('local event identity conflict')
            return False
        db.execute('INSERT INTO observations(source_id,event_id,body) VALUES (?,?,?)', (source_id, event_id, body))
        return True

    def enqueue(self, source_id, event):
        with self._write() as db:
            return self._add(db, source_id, event)

    def enqueue_native_hook(self, source_id, event):
        """Persist first capture and its identity together, retaining the key after ACK."""
        with self._write() as db:
            result = db.execute(
                'INSERT OR IGNORE INTO seen_native_hooks VALUES (?,?)', (source_id, event['event_id']),
            )
            if result.rowcount == 0:
                return False
            return self._add(db, source_id, event)

    def enqueue_many(self, source_id, events):
        with self._write() as db:
            for event in events:
                self._add(db, source_id, event)

    def next_batch(self, source_id):
        """Reserve one batch without duplicating the payload on disk."""
        if not valid_id(source_id):
            raise ValueError('invalid observation source')
        with self._write() as db:
            current = db.execute('SELECT batch_id,last_seq FROM batches WHERE source_id=?', (source_id,)).fetchone()
            if current:
                rows = db.execute(
                    'SELECT seq,body FROM observations WHERE source_id=? AND seq<=? ORDER BY seq',
                    (source_id, current[1]),
                )
            else:
                rows = db.execute(
                    'SELECT seq,body FROM observations WHERE source_id=? ORDER BY seq LIMIT ?',
                    (source_id, MAX_BATCH_EVENTS),
                )
            events = []
            last_seq = 0
            size = envelope_bytes(source_id)
            for seq, body in rows:
                size += len(body.encode()) + bool(events)
                if size > MAX_BATCH_BYTES:
                    break
                events.append(json.loads(body))
                last_seq = seq
            if not events:
                return None
            if current:
                batch_id = current[0]
            else:
                batch_id = stable_id('batch', [source_id, events])
                db.execute('INSERT INTO batches VALUES (?,?,?)', (batch_id, source_id, last_seq))
        return batch_envelope(source_id, batch_id, events)

    def acknowledge(self, batch, receipt):
        """Reclaim only the exact persisted event set in the original batch."""
        ids = {event['event_id'] for event in batch['events']}
        if (not isinstance(receipt, dict) or receipt.get('persisted') is not True
                or receipt.get('source_id') != batch['source_id'] or receipt.get('batch_id') != batch['batch_id']):
            raise ValueError('receipt does not confirm the persisted source and batch')
        accepted = receipt.get('accepted_event_ids')
        duplicate = receipt.get('duplicate_event_ids')
        if not isinstance(accepted, list) or not isinstance(duplicate, list):
            raise ValueError('receipt requires explicit accepted and duplicate event lists')
        returned = accepted + duplicate
        if (not all(valid_id(item) for item in returned)
                or len(returned) != len(ids) or set(returned) != ids):
            raise ValueError('receipt does not confirm the exact event set')
        with self._write() as db:
            reservation = db.execute(
                'SELECT last_seq FROM batches WHERE source_id=? AND batch_id=?',
                (batch['source_id'], batch['batch_id']),
            ).fetchone()
            if reservation is None:
                return False
            bound = (batch['source_id'], reservation[0])
            persisted_ids = {row[0] for row in db.execute(
                'SELECT event_id FROM observations WHERE source_id=? AND seq<=?', bound,
            )}
            if persisted_ids != ids:
                raise ValueError('local reservation does not match the acknowledged batch')
            db.execute('DELETE FROM observations WHERE source_id=? AND seq<=?', bound)
            db.execute('DELETE FROM batches WHERE source_id=? AND batch_id=?', (batch['source_id'], batch['batch_id']))
            return True
