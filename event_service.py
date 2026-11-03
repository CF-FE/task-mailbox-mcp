"""Opt-in, job-scoped MCP Events; notifications never execute or acknowledge jobs."""

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from urllib.parse import urlsplit

from mcp.shared.exceptions import MCPError
from cryptography.fernet import InvalidToken

from callback_http import CallbackError, PinnedHTTPSPoster
from event_store import EventCapacityError
from mailbox_store import IDENTIFIER, SHA256


EVENT_NAME = 'mailbox.result_ready'
EVENT_DEFINITION = {
    'name': EVENT_NAME,
    'description': 'A result version is available for the exact subscribed project and job. '
                   'This notification contains metadata, not the result or a read confirmation.',
    'delivery': ['webhook'],
    'inputSchema': {'type': 'object', 'properties': {
        'project_id': {'type': 'string'}, 'job_id': {'type': 'string'}},
        'required': ['project_id', 'job_id'], 'additionalProperties': False},
    'payloadSchema': {'type': 'object', 'properties': {
        'project_id': {'type': 'string'}, 'job_id': {'type': 'string'},
        'payload_hash': {'type': 'string'}, 'version': {'type': 'integer'},
        'result_hash': {'type': 'string'}, 'status': {'type': 'string'}},
        'required': ['project_id', 'job_id', 'payload_hash', 'version', 'result_hash', 'status'],
        'additionalProperties': False},
}


def fail(reason, code=-32602):
    raise MCPError(code=code, message='Mailbox events request rejected', data={'reason': reason})


def load_policy(path):
    """Read an explicit private policy on every operation; never reuse stale authority."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('EVENT_POLICY_UNSAFE')
    parent = path.parent.stat()
    if path.parent.is_symlink() or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError('EVENT_POLICY_UNSAFE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise ValueError('EVENT_POLICY_UNSAFE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            policy = json.loads(stream.read(65537))
    finally:
        os.close(fd)
    if (not isinstance(policy, dict) or set(policy) != {'version', 'allowed_callback_hosts', 'jobs'}
            or type(policy['version']) is not int or policy['version'] != 1
            or not isinstance(policy['allowed_callback_hosts'], list)
            or not 1 <= len(policy['allowed_callback_hosts']) <= 16
            or not isinstance(policy['jobs'], list) or len(policy['jobs']) > 64):
        raise ValueError('EVENT_POLICY_INVALID')
    hosts = policy['allowed_callback_hosts']
    if any(not isinstance(host, str) or not host.isascii() or host != host.lower()
           or not host or len(host) > 253 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for c in host)
           or host.startswith('.') or host.endswith('.') or '*' in host for host in hosts):
        raise ValueError('EVENT_POLICY_INVALID')
    PinnedHTTPSPoster(hosts)  # Validate DNS-name syntax without resolving or connecting.
    seen = set()
    for grant in policy['jobs']:
        if (not isinstance(grant, dict) or set(grant) != {'subject', 'project_id', 'repository', 'job_id', 'payload_hash'}
                or any(not isinstance(grant[k], str) or not grant[k].strip()
                       for k in ('subject', 'project_id', 'repository', 'job_id', 'payload_hash'))
                or not IDENTIFIER.fullmatch(grant['project_id']) or not IDENTIFIER.fullmatch(grant['job_id'])
                or not SHA256.fullmatch(grant['payload_hash'])
                or grant['repository'] != 'CF-FE/' + grant['project_id']):
            raise ValueError('EVENT_POLICY_INVALID')
        identity = (grant['subject'], grant['project_id'], grant['job_id'])
        if identity in seen:
            raise ValueError('EVENT_POLICY_INVALID')
        seen.add(identity)
    return policy


def signing_key(secret):
    if not isinstance(secret, str) or not secret.startswith('whsec_') or len(secret) > 100:
        fail('INVALID_SIGNING_SECRET')
    try:
        key = base64.b64decode(secret[6:], validate=True)
    except (ValueError, binascii.Error):
        fail('INVALID_SIGNING_SECRET')
    if not 24 <= len(key) <= 64:
        fail('INVALID_SIGNING_SECRET')
    return key


def signed_headers(event_id, body, secret, subscription_id, signed_at, old_secret=None):
    """Standard Webhooks v1: sign exactly the bytes subsequently sent."""
    stamp = str(int(signed_at))
    content = event_id.encode('ascii') + b'.' + stamp.encode('ascii') + b'.' + body
    keys = [secret] + ([old_secret] if old_secret and old_secret != secret else [])
    signatures = ['v1,' + base64.b64encode(hmac.new(signing_key(key), content, hashlib.sha256).digest()).decode('ascii')
                  for key in keys]
    return {'Content-Type': 'application/json', 'webhook-id': event_id,
            'webhook-timestamp': stamp, 'webhook-signature': ' '.join(signatures),
            'X-MCP-Subscription-Id': subscription_id}


class EventService:
    def __init__(self, store, mailbox, policy_loader, *, poster_factory=PinnedHTTPSPoster, clock=time.time):
        self.store, self.mailbox, self.policy_loader = store, mailbox, policy_loader
        self.poster_factory, self.clock = poster_factory, clock
        self._verified = {}
        self._verifications = asyncio.Semaphore(2)
        self._changes = asyncio.Lock()
        self._rotation = 0
        self.health = {'state': 'NOT_RUN'}

    def authorize(self, principal, arguments, policy):
        if (not isinstance(arguments, dict) or set(arguments) != {'project_id', 'job_id'}
                or any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v) for v in arguments.values())):
            fail('INVALID_FILTER')
        subject = json.loads(principal)['subject']
        grant = next((g for g in policy['jobs'] if (g['subject'], g['project_id'], g['job_id'])
                      == (subject, arguments['project_id'], arguments['job_id'])), None)
        if grant is None:
            fail('ACCESS_DENIED', -32001)
        try:
            job = self.mailbox.get_job(arguments['job_id'])
        except KeyError:
            return grant  # Exact future job/hash was approved before its submission.
        payload = job['payload']
        tracking = payload.get('tracking')
        if (job['payload_hash'] != grant['payload_hash'] or not isinstance(tracking, dict)
                or payload.get('target', {}).get('project_id') != arguments['project_id']
                or tracking.get('repository') != grant['repository']):
            fail('JOB_BINDING_MISMATCH', -32001)
        return grant

    async def handle(self, method, principal, params):
        try:
            if method == 'events/list':
                return await self._handle(method, principal, params)
            # A late callback verification must not reactivate an unsubscribed
            # identity. One process owns the service; serialize changes through it.
            async with self._changes:
                return await self._handle(method, principal, params)
        except sqlite3.Error:
            fail('STORAGE_UNAVAILABLE', -32002)

    async def _handle(self, method, principal, params):
        try:
            policy = self.policy_loader()
        except (OSError, ValueError):
            fail('POLICY_UNAVAILABLE', -32001)
        if method == 'events/list':
            if params and params != {'cursor': None}:
                fail('INVALID_LIST_ARGUMENTS')
            subject = json.loads(principal)['subject']
            return {'events': [EVENT_DEFINITION] if any(g['subject'] == subject for g in policy['jobs']) else []}
        allowed = {'name', 'arguments', 'delivery', 'cursor', 'ttlMs'} if method == 'events/subscribe' else {'name', 'arguments', 'delivery'}
        if (not isinstance(params, dict) or not {'name', 'arguments', 'delivery'} <= set(params)
                or set(params) - allowed or params['name'] != EVENT_NAME):
            fail('INVALID_SUBSCRIPTION')
        delivery = params['delivery']
        subscribe = method == 'events/subscribe'
        if (method not in ('events/subscribe', 'events/unsubscribe') or not isinstance(delivery, dict)
                or set(delivery) != ({'mode', 'url', 'secret'} if subscribe else {'mode', 'url'})
                or delivery['mode'] != 'webhook' or not isinstance(delivery['url'], str)):
            fail('INVALID_DELIVERY')
        url = delivery['url']
        if not subscribe:
            # The authenticated owner can stop an existing subscription after
            # its resource or callback host has been revoked. No outbound I/O.
            return self.store.close(principal, EVENT_NAME, params['arguments'], url)
        self.authorize(principal, params['arguments'], policy)
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == 'https' and parsed.hostname in policy['allowed_callback_hosts']
                     and parsed.port in (None, 443) and not parsed.username and not parsed.password
                     and not parsed.fragment and len(url) <= 4096 and all(ord(c) >= 33 and ord(c) != 127 for c in url))
        except ValueError:
            valid = False
        if not valid:
            fail('CALLBACK_NOT_ALLOWED', -32015)
        secret = delivery['secret']
        signing_key(secret)
        ttl = params.get('ttlMs', 86400000)
        if ttl is not None and (type(ttl) is not int or ttl <= 0):
            fail('INVALID_TTL')
        cursor = params.get('cursor')
        if cursor is not None and (not isinstance(cursor, str) or not cursor.isascii()
                                   or not cursor.isdigit() or len(cursor) > 19 or int(cursor) > 2**63 - 1):
            fail('INVALID_CURSOR')
        identity = self.store.identity(principal, EVENT_NAME, params['arguments'], url)
        cache_key = hashlib.sha256((principal + '\0' + url + '\0' + secret).encode()).hexdigest()
        if self._verified.get(cache_key, 0) <= self.clock():
            body = json.dumps({'type': 'verification', 'challenge': secrets.token_hex(32)}, separators=(',', ':')).encode()
            challenge = json.loads(body)['challenge']
            headers = signed_headers('verify_' + secrets.token_hex(16), body, secret, identity, self.clock())
            try:
                async with self._verifications:
                    status, reply = await asyncio.to_thread(self.poster_factory(policy['allowed_callback_hosts']).post, url, body, headers)
                response = json.loads(reply) if 200 <= status < 300 else None
                if (not isinstance(response, dict) or not isinstance(response.get('challenge'), str)
                        or not hmac.compare_digest(response['challenge'], challenge)):
                    fail('challenge_failed', -32015)
            except CallbackError as error:
                fail(error.reason.lower(), -32015)
            except (ValueError, UnicodeError):
                fail('challenge_failed', -32015)
            self._verified = {key: expiry for key, expiry in self._verified.items() if expiry > self.clock()}
            if len(self._verified) >= 128:
                self._verified.pop(next(iter(self._verified)))
            self._verified[cache_key] = self.clock() + 600
        try:
            current_policy = self.policy_loader()
        except (OSError, ValueError):
            fail('POLICY_UNAVAILABLE', -32001)
        self.authorize(principal, params['arguments'], current_policy)
        if parsed.hostname not in current_policy['allowed_callback_hosts']:
            fail('CALLBACK_NOT_ALLOWED', -32015)
        try:
            sub = self.store.save(principal, EVENT_NAME, params['arguments'], url, secret,
                                  ttl_ms=ttl, cursor=cursor)
        except EventCapacityError:
            fail('CAPACITY_REACHED', -32002)
        except ValueError:
            fail('SUBSCRIPTION_CONFLICT')
        return {'id': sub['id'], 'refreshBefore': datetime.fromtimestamp(sub['expires_at'], timezone.utc).isoformat(),
                'cursor': self.store.resume_cursor(sub['id']), 'truncated': False}

    async def tick(self):
        """One bounded replay/delivery round. Original result tools remain authority."""
        policy = self.policy_loader()  # Failure aborts the round; no cached authority.
        active = self.store.active()
        selected = (active[self._rotation:] + active[:self._rotation])[:8]
        self._rotation = (self._rotation + 8) % max(1, len(active))
        capacity_reached = False
        for sub in selected:
            try:
                self.authorize(sub['owner'], sub['arguments'], policy)
            except MCPError:
                self.store.close(sub['owner'], sub['name'], sub['arguments'], sub['url'])
                continue
            try:
                self.store.prepare(sub['id'])
            except EventCapacityError:
                capacity_reached = True  # Preserve existing outbox delivery.
        sent = 0
        for delivery in self.store.pending(limit=8):
            policy = self.policy_loader()
            try:
                self.authorize(delivery['owner'], delivery['arguments'], policy)
            except MCPError:
                self.store.close(delivery['owner'], delivery['name'], delivery['arguments'], delivery['url'])
                continue
            delivery = self.store.mark_attempt(delivery['delivery_id'])
            if not delivery:
                continue
            old = delivery.get('old_secret') if delivery.get('rotation_until', 0) > self.clock() else None
            headers = signed_headers(delivery['event_id'], delivery['body'], delivery['secret'],
                                     delivery['subscription_id'], self.clock(), old)
            try:
                status, _ = await asyncio.to_thread(self.poster_factory(policy['allowed_callback_hosts']).post,
                                                    delivery['url'], delivery['body'], headers)
                self.store.finish(delivery['delivery_id'], status, 'HTTP_RESPONSE')
                sent += int(200 <= status < 300)
            except CallbackError as error:
                self.store.finish(delivery['delivery_id'], None, error.reason)
        self.health = {'state': 'CAPACITY_REACHED' if capacity_reached else 'ROUND_COMPLETE',
                       'sent': sent, 'observed_at': self.clock()}


def event_lifespan(service):
    @asynccontextmanager
    async def lifespan(server):
        async def worker():
            while True:
                try:
                    await service.tick()
                except (OSError, ValueError, sqlite3.Error, InvalidToken):
                    service.health = {'state': 'POLICY_OR_STORAGE_UNAVAILABLE', 'observed_at': service.clock()}
                    logging.getLogger(__name__).warning('Event delivery paused: policy or storage unavailable')
                await asyncio.sleep(10)
        task = asyncio.create_task(worker())
        def completed(future):
            if not future.cancelled() and future.exception() is not None:
                service.health = {'state': 'UNEXPECTED_FAILURE', 'observed_at': service.clock()}
                logging.getLogger(__name__).error('Event delivery stopped: unexpected failure')
        task.add_done_callback(completed)
        try:
            yield {}
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    return lifespan
