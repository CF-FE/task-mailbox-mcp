"""Bounded, restart-safe MCP event subscriptions and delivery attempts.

The mailbox owns the database and its quota. This module neither performs
network I/O nor grants source identity: the service rechecks its private policy
before preparing and sending. Production supplies an authenticated cipher.
"""

import hashlib
import json
import math
import re
import time


NAME = 'mailbox.result_ready'
TOKEN = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z', re.ASCII)
REASON = re.compile(r'[A-Z0-9_]{1,64}\Z', re.ASCII)
MAX_CURSOR = 2**63 - 1
BACKOFF = (5, 30, 120, 300, 900)


class EventCapacityError(ValueError):
    """Admission is full; existing deliveries remain eligible for sending."""


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _limit(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('invalid event query limit')
    return value


def _notification(name, args, row):
    wire_id = 'mbe_' + hashlib.sha256(row['event_id'].encode('utf-8')).hexdigest()
    data = {key: row[key] for key in ('job_id', 'payload_hash', 'version', 'result_hash', 'status')}
    data['project_id'] = args['project_id']
    return canonical({'eventId': wire_id, 'name': name, 'timestamp': row['created_at'],
                      'data': data, 'cursor': str(row['cursor'])})


class EventStore:
    def __init__(self, mailbox, cipher, clock=time.time,
                 max_subscriptions=64, max_deliveries=1024):
        self.mailbox, self.cipher, self.clock = mailbox, cipher, clock
        self.max_subscriptions = _limit(max_subscriptions, 1024)
        self.max_deliveries = _limit(max_deliveries, 100000)
        with mailbox._transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS event_subscriptions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                arguments_json TEXT NOT NULL, callback_hash TEXT NOT NULL,
                callback_enc BLOB NOT NULL, secret_enc BLOB NOT NULL,
                old_secret_enc BLOB, rotation_until REAL NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL, after_cursor INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'ACTIVE')''')
            db.execute('''CREATE TABLE IF NOT EXISTS event_deliveries (
                delivery_id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL REFERENCES event_subscriptions(id),
                event_id TEXT NOT NULL REFERENCES events(event_id),
                event_cursor INTEGER NOT NULL, body BLOB NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, last_status INTEGER,
                last_reason TEXT NOT NULL DEFAULT '',
                UNIQUE(subscription_id,event_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS event_delivery_due ON event_deliveries(state,next_attempt)')
            db.execute('CREATE INDEX IF NOT EXISTS event_delivery_subscription ON event_deliveries(subscription_id,event_cursor)')

    @staticmethod
    def identity(owner, name, args, url):
        if (not isinstance(owner, str) or not owner.strip() or len(owner) > 2048
                or name != NAME or not isinstance(args, dict)
                or set(args) != {'project_id', 'job_id'}
                or any(not isinstance(v, str) or TOKEN.fullmatch(v) is None for v in args.values())
                or not isinstance(url, str) or not url or len(url) > 4096):
            raise ValueError('invalid event subscription identity')
        return 'sub_' + hashlib.sha256(canonical([owner, name, args, url])).hexdigest()

    def _now(self):
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('invalid event clock')
        return value

    def _subscription(self, row):
        if row is None:
            return None
        old = row['old_secret_enc']
        result = {'id': row['id'], 'owner': row['owner'], 'name': row['name'],
                'arguments': json.loads(row['arguments_json']),
                'url': self.cipher.decrypt(row['callback_enc']).decode('utf-8'),
                'secret': self.cipher.decrypt(row['secret_enc']).decode('utf-8'),
                'old_secret': self.cipher.decrypt(old).decode('utf-8') if old is not None else None,
                'rotation_until': row['rotation_until'], 'expires_at': row['expires_at'],
                'after_cursor': row['after_cursor'], 'state': row['state']}
        if (self.identity(result['owner'], result['name'], result['arguments'], result['url']) != result['id']
                or hashlib.sha256(result['url'].encode()).hexdigest() != row['callback_hash']):
            raise ValueError('stored event subscription identity conflicts')
        return result

    @staticmethod
    def _expire(db, now):
        db.execute("UPDATE event_subscriptions SET state='EXPIRED' WHERE state='ACTIVE' AND expires_at<=?", (now,))
        db.execute("UPDATE event_deliveries SET state='EXHAUSTED' WHERE state='PENDING' AND attempts>=8 AND next_attempt<=?", (now,))
        db.execute('UPDATE event_subscriptions SET old_secret_enc=NULL,rotation_until=0 WHERE old_secret_enc IS NOT NULL AND rotation_until<=?', (now,))

    def get(self, owner, name, args, url):
        sub_id = self.identity(owner, name, args, url)
        with self.mailbox._transaction() as db:
            self._expire(db, self._now())
            return self._subscription(db.execute('SELECT * FROM event_subscriptions WHERE id=?', (sub_id,)).fetchone())

    def save(self, owner, name, args, url, secret, ttl_ms=86400000, cursor=None):
        sub_id = self.identity(owner, name, args, url)
        if not isinstance(secret, str) or not secret or len(secret) > 128:
            raise ValueError('invalid event signing secret')
        if ttl_ms is None:
            ttl_ms = 7 * 86400000
        if type(ttl_ms) is not int or ttl_ms <= 0:
            raise ValueError('invalid event subscription lifetime')
        lifetime = min(max(ttl_ms, 60000), 7 * 86400000) / 1000
        if cursor is not None and (not isinstance(cursor, str) or not cursor.isascii()
                                   or not cursor.isdecimal() or len(cursor) > 19
                                   or int(cursor) > MAX_CURSOR):
            raise ValueError('invalid event replay cursor')
        now = self._now()
        with self.mailbox._transaction() as db:
            old = db.execute('SELECT * FROM event_subscriptions WHERE id=?', (sub_id,)).fetchone()
            if old is None:
                if db.execute('SELECT COUNT(*) FROM event_subscriptions').fetchone()[0] >= self.max_subscriptions:
                    raise EventCapacityError('event subscription capacity reached')
                high = db.execute('SELECT COALESCE(MAX(cursor),0) FROM events').fetchone()[0]
                after = 0 if cursor is None else int(cursor)
                if after > high:
                    raise ValueError('event replay cursor exceeds stored history')
                db.execute('''INSERT INTO event_subscriptions
                    (id,owner,name,arguments_json,callback_hash,callback_enc,secret_enc,expires_at,after_cursor)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (sub_id, owner, name, canonical(args).decode(), hashlib.sha256(url.encode()).hexdigest(),
                     self.cipher.encrypt(url.encode()), self.cipher.encrypt(secret.encode()), now + lifetime, after))
            else:
                if cursor is not None and int(cursor) > old['after_cursor']:
                    raise ValueError('event replay cursor would skip stored work')
                # A collision or damaged row is a conflict, never permission to overwrite.
                prior = self._subscription(old)
                if (prior['owner'], prior['name'], prior['arguments'], prior['url']) != (owner, name, args, url):
                    raise ValueError('event subscription identity conflicts')
                changed = prior['secret'] != secret
                old_secret = old['secret_enc'] if changed else old['old_secret_enc']
                rotation = now + 300 if changed else old['rotation_until']
                db.execute("""UPDATE event_subscriptions SET secret_enc=?, old_secret_enc=?,
                    rotation_until=?, expires_at=?, state='ACTIVE' WHERE id=?""",
                    (self.cipher.encrypt(secret.encode()), old_secret, rotation, now + lifetime, sub_id))
            return self._subscription(db.execute('SELECT * FROM event_subscriptions WHERE id=?', (sub_id,)).fetchone())

    def close(self, owner, name, args, url):
        sub_id = self.identity(owner, name, args, url)
        with self.mailbox._transaction() as db:
            db.execute("UPDATE event_subscriptions SET state='CLOSED' WHERE id=?", (sub_id,))
            db.execute("UPDATE event_deliveries SET state='CANCELLED' WHERE subscription_id=? AND state='PENDING'", (sub_id,))
        return {}

    def active(self, limit=64):
        _limit(limit, 1024)
        with self.mailbox._transaction() as db:
            self._expire(db, self._now())
            return [self._subscription(r) for r in db.execute(
                "SELECT * FROM event_subscriptions WHERE state='ACTIVE' ORDER BY id LIMIT ?", (limit,))]

    def prepare(self, sub_id, limit=25):
        _limit(limit, 100)
        now = self._now()
        with self.mailbox._transaction() as db:
            self._expire(db, now)
            sub = db.execute("SELECT * FROM event_subscriptions WHERE id=? AND state='ACTIVE'", (sub_id,)).fetchone()
            if sub is None:
                return 0
            args = json.loads(sub['arguments_json'])
            rows = db.execute('''SELECT e.cursor,e.event_id,e.created_at,r.version,r.result_hash,
                       r.status,j.job_id,j.payload_hash FROM events e
                       JOIN results r ON r.event_id=e.event_id JOIN jobs j ON j.job_id=e.job_id
                       WHERE e.event_type='RESULT_REPORTED' AND e.job_id=? AND e.cursor>?
                       ORDER BY e.cursor LIMIT ?''', (args['job_id'], sub['after_cursor'], limit)).fetchall()
            count = db.execute('SELECT COUNT(*) FROM event_deliveries').fetchone()[0]
            created = 0
            for row in rows:
                delivery_id = 'del_' + hashlib.sha256(canonical([sub_id, row['event_id']])).hexdigest()
                previous = db.execute('SELECT body FROM event_deliveries WHERE delivery_id=?', (delivery_id,)).fetchone()
                body = _notification(sub['name'], args, row)
                if len(body) > 262144:
                    raise ValueError('event notification exceeds protocol limit')
                if previous is not None:
                    if previous['body'] != body:
                        raise ValueError('event delivery identity conflicts')
                else:
                    if count >= self.max_deliveries:
                        raise EventCapacityError('event delivery capacity reached')
                    db.execute('''INSERT INTO event_deliveries
                        (delivery_id,subscription_id,event_id,event_cursor,body) VALUES(?,?,?,?,?)''',
                        (delivery_id, sub_id, row['event_id'], row['cursor'], body))
                    created += 1
                    count += 1
                db.execute('UPDATE event_subscriptions SET after_cursor=? WHERE id=?', (row['cursor'], sub_id))
            return created

    def _delivery(self, db, row):
        sub = db.execute('SELECT * FROM event_subscriptions WHERE id=?', (row['subscription_id'],)).fetchone()
        result = self._subscription(sub)
        source = db.execute('''SELECT e.cursor,e.event_id,e.created_at,r.version,r.result_hash,
                    r.status,j.job_id,j.payload_hash FROM events e
                    JOIN results r ON r.event_id=e.event_id JOIN jobs j ON j.job_id=e.job_id
                    WHERE e.event_type='RESULT_REPORTED' AND e.event_id=?''', (row['event_id'],)).fetchone()
        if (source is None or source['cursor'] != row['event_cursor']
                or source['job_id'] != result['arguments']['job_id']
                or _notification(result['name'], result['arguments'], source) != row['body']):
            raise ValueError('stored event delivery identity conflicts')
        result.update({key: row[key] for key in ('delivery_id', 'subscription_id', 'event_id', 'body',
                                                'attempts', 'next_attempt', 'last_status', 'last_reason')})
        result['delivery_state'] = row['state']
        result['source_event_id'] = row['event_id']
        result['event_id'] = json.loads(row['body'])['eventId']
        return result

    def pending(self, limit=8):
        _limit(limit, 100)
        now = self._now()
        with self.mailbox._transaction() as db:
            self._expire(db, now)
            rows = db.execute('''SELECT d.* FROM event_deliveries d
                JOIN event_subscriptions s ON s.id=d.subscription_id
                WHERE d.state='PENDING' AND d.attempts<8 AND d.next_attempt<=?
                AND s.state='ACTIVE' AND s.expires_at>?
                ORDER BY d.next_attempt,d.event_cursor,d.delivery_id LIMIT ?''', (now, now, limit)).fetchall()
            return [self._delivery(db, row) for row in rows]

    def mark_attempt(self, delivery_id):
        now = self._now()
        with self.mailbox._transaction() as db:
            self._expire(db, now)
            row = db.execute('''SELECT d.* FROM event_deliveries d
                JOIN event_subscriptions s ON s.id=d.subscription_id WHERE d.delivery_id=?
                AND d.state='PENDING' AND d.attempts<8 AND d.next_attempt<=?
                AND s.state='ACTIVE' AND s.expires_at>?''', (delivery_id, now, now)).fetchone()
            if row is None:
                return None
            # A crash after this commit may have delivered. Recovery resends
            # this event; it does not create another model turn or result.
            db.execute('UPDATE event_deliveries SET attempts=attempts+1,next_attempt=? WHERE delivery_id=?',
                       (now + 30, delivery_id))
            return self._delivery(db, db.execute('SELECT * FROM event_deliveries WHERE delivery_id=?', (delivery_id,)).fetchone())

    def finish(self, delivery_id, status, reason):
        if ((status is not None and (type(status) is not int or not 100 <= status <= 599))
                or not isinstance(reason, str) or REASON.fullmatch(reason) is None):
            raise ValueError('invalid event delivery outcome')
        now = self._now()
        with self.mailbox._transaction() as db:
            row = db.execute('SELECT * FROM event_deliveries WHERE delivery_id=?', (delivery_id,)).fetchone()
            if row is None:
                raise KeyError(delivery_id)
            if row['attempts'] < 1:
                raise ValueError('event outcome has no durable attempt')
            if row['state'] == 'SENT':
                return {'delivery_id': delivery_id, 'state': 'SENT', 'attempts': row['attempts']}
            if status is not None and 200 <= status < 300:
                state, due = 'SENT', now
            elif row['state'] != 'PENDING':
                state, due = row['state'], row['next_attempt']
            elif status is not None and 400 <= status < 500 and status not in (408, 425, 429):
                state, due = 'STOPPED', now
            elif row['attempts'] >= 8:
                state, due = 'EXHAUSTED', now
            else:
                state = 'PENDING'
                due = now + BACKOFF[min(row['attempts'] - 1, len(BACKOFF) - 1)]
            db.execute('''UPDATE event_deliveries SET state=?,next_attempt=?,last_status=?,last_reason=?
                WHERE delivery_id=?''', (state, due, status, reason, delivery_id))
            if status == 410:
                db.execute("UPDATE event_subscriptions SET state='STOPPED' WHERE id=?", (row['subscription_id'],))
                db.execute("UPDATE event_deliveries SET state='CANCELLED' WHERE subscription_id=? AND state='PENDING'",
                           (row['subscription_id'],))
            return {'delivery_id': delivery_id, 'state': state, 'attempts': row['attempts']}

    def resume_cursor(self, sub_id):
        with self.mailbox._transaction() as db:
            row = db.execute('SELECT after_cursor FROM event_subscriptions WHERE id=?', (sub_id,)).fetchone()
            if row is None:
                raise KeyError(sub_id)
            unsent = db.execute("SELECT MIN(event_cursor) FROM event_deliveries WHERE subscription_id=? AND state!='SENT'", (sub_id,)).fetchone()[0]
            return str(min(row['after_cursor'], unsent - 1) if unsent is not None else row['after_cursor'])
