"""Allowlisted, DNS-pinned HTTPS POSTs using only the Python standard library.

There are no proxies, redirects, or automatic retries. Every POST validates all
DNS answers before dialing one checked numeric address; TLS keeps the DNS name.
At most two daemon DNS lookups may remain blocked after callers time out. System
getaddrinfo cannot be cancelled; those lookups retain their slots until return.
An active HTTP transaction has one short-lived deadline watchdog which shuts down
its pinned socket, so slow response headers cannot reset the total wait budget.
"""

import http.client
import ipaddress
import math
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit


REASONS = frozenset({
    'INVALID_CONFIGURATION', 'INVALID_URL', 'HOST_NOT_ALLOWED', 'DNS_FAILED',
    'ADDRESS_NOT_PUBLIC', 'REQUEST_INVALID', 'CONNECT_FAILED', 'TLS_FAILED',
    'HTTP_FAILED', 'RESPONSE_TOO_LARGE', 'TIMEOUT',
})
_DNS_SLOTS = threading.BoundedSemaphore(2)
_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.ASCII)
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", re.ASCII)


class CallbackError(ValueError):
    """A fixed safe reason, with no URL, headers, or underlying exception text."""

    def __init__(self, reason):
        if reason not in REASONS:
            raise ValueError('invalid callback error reason')
        self.reason = reason
        super().__init__(reason)


def _dns_host(value):
    if not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 253:
        return None
    host = value.lower()
    if not all(_LABEL.fullmatch(label) for label in host.split('.')):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CallbackError('TIMEOUT')
    return remaining


def _resolve(resolver, host, deadline):
    """Bound both caller wait and outstanding uninterruptible resolver threads."""
    if not _DNS_SLOTS.acquire(timeout=_remaining(deadline)):
        raise CallbackError('TIMEOUT')
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            try:
                outcome = True, resolver(host, 443, type=socket.SOCK_STREAM)
            except BaseException as error:
                outcome = False, error
            result.put_nowait(outcome)
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=lookup, name='callback-dns', daemon=True).start()
    except BaseException:
        _DNS_SLOTS.release()
        raise
    timed_out = False
    try:
        ok, value = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        timed_out = True
    if timed_out:
        raise CallbackError('TIMEOUT')
    _remaining(deadline)
    if not ok:
        if isinstance(value, TimeoutError):
            raise CallbackError('TIMEOUT')
        if isinstance(value, OSError):
            raise CallbackError('DNS_FAILED')
        raise value  # A programming error in an injected resolver is not hidden.
    if not isinstance(value, (list, tuple)) or not value:
        raise CallbackError('DNS_FAILED')
    checked = []
    invalid = False
    for item in value:
        if (not isinstance(item, tuple) or len(item) != 5
                or item[0] not in (socket.AF_INET, socket.AF_INET6)
                or item[1] != socket.SOCK_STREAM
                or item[2] not in (0, socket.IPPROTO_TCP)
                or not isinstance(item[4], tuple) or len(item[4]) < 2
                or not isinstance(item[4][0], str) or '%' in item[4][0]
                or item[4][1] != 443):
            invalid = True
            break
        try:
            address = ipaddress.ip_address(item[4][0])
        except ValueError:
            invalid = True
            break
        if ((item[0] == socket.AF_INET) != (address.version == 4)):
            invalid = True
            break
        public = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        public = public or address
        if (not public.is_global or public.is_private or public.is_loopback
                or public.is_link_local or public.is_reserved or public.is_multicast
                or public.is_unspecified):
            invalid = True
            break
        checked.append(str(address))
    if invalid:
        raise CallbackError('ADDRESS_NOT_PUBLIC')
    return checked


class PinnedHTTPSPoster:
    def __init__(self, allowed_hosts=(), timeout=10, max_response_bytes=65536,
                 resolver=socket.getaddrinfo, connection_factory=None):
        if (not isinstance(allowed_hosts, (list, tuple, set, frozenset))
                or any(_dns_host(host) is None for host in allowed_hosts)
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0
                or type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 65536
                or not callable(resolver)
                or (connection_factory is not None and not callable(connection_factory))):
            raise CallbackError('INVALID_CONFIGURATION')
        self.allowed_hosts = frozenset(_dns_host(host) for host in allowed_hosts)
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.resolver = resolver
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    def post(self, url, body, headers):
        if (not isinstance(url, str) or not 1 <= len(url) <= 4096
                or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
            raise CallbackError('INVALID_URL')
        parsed, host, port = None, None, None
        try:
            parsed = urlsplit(url)
            port = parsed.port
            host = _dns_host(parsed.hostname)
        except ValueError:
            pass
        if (parsed is None or host is None or parsed.scheme != 'https'
                or parsed.username is not None or parsed.password is not None
                or '#' in url or parsed.fragment or port not in (None, 443)
                or parsed.netloc.lower() not in (host, host + ':443')):
            raise CallbackError('INVALID_URL')
        if host not in self.allowed_hosts:
            raise CallbackError('HOST_NOT_ALLOWED')
        path = (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')
        if not path.isascii():
            raise CallbackError('INVALID_URL')
        if (not isinstance(body, bytes) or not isinstance(headers, dict)
                or any(not isinstance(key, str) or not _HEADER.fullmatch(key)
                       or key.lower() in {'host', 'content-length', 'transfer-encoding', 'connection'}
                       or not isinstance(value, str)
                       or any(ord(char) < 32 or ord(char) == 127 or ord(char) > 255 for char in value)
                       for key, value in headers.items())):
            raise CallbackError('REQUEST_INVALID')
        deadline = time.monotonic() + self.timeout
        checked = _resolve(self.resolver, host, deadline)

        def pinned_connection(address, timeout=None, source_address=None, **kwargs):
            if address != (host, 443):
                raise CallbackError('CONNECT_FAILED')
            sock = socket.create_connection((checked[0], 443), _remaining(deadline), source_address)
            try:
                sock.settimeout(_remaining(deadline))
            except BaseException:
                sock.close()
                raise
            return sock

        connection, response, reason, answer = None, None, None, None
        watchdog, expired = None, threading.Event()
        stage = 'CONNECT_FAILED'
        try:
            try:
                connection = self.connection_factory(host, port=443, timeout=_remaining(deadline),
                                                     context=ssl.create_default_context())
                connection._create_connection = pinned_connection
                connection.connect()
                active_socket = connection.sock
                def stop_waiting():
                    expired.set()
                    try:
                        active_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass  # Completion/close raced the exact deadline.
                watchdog = threading.Timer(_remaining(deadline), stop_waiting)
                watchdog.daemon = True
                watchdog.start()
                stage = 'HTTP_FAILED'
                active_socket.settimeout(_remaining(deadline))
                connection.request('POST', path, body=body, headers=dict(headers))
                active_socket.settimeout(_remaining(deadline))
                response = connection.getresponse()
                if type(response.status) is not int or not 100 <= response.status <= 599:
                    raise CallbackError('HTTP_FAILED')
                chunks, size = [], 0
                while not response.isclosed():
                    # getresponse may detach a Connection: close socket while
                    # its response file still owns the live descriptor.
                    active_socket.settimeout(_remaining(deadline))
                    chunk = response.read1(self.max_response_bytes + 1 - size)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise CallbackError('RESPONSE_TOO_LARGE')
                    chunks.append(chunk)
                _remaining(deadline)
                answer = response.status, b''.join(chunks)
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if response is not None:
                    response.close()
                if connection is not None:
                    connection.close()
        except CallbackError as error:
            reason = error.reason
        except TimeoutError:
            reason = 'TIMEOUT'
        except ssl.SSLError:
            reason = 'TLS_FAILED'
        except http.client.HTTPException:
            reason = 'HTTP_FAILED'
        except OSError:
            reason = stage
        if reason is not None:
            if expired.is_set() or time.monotonic() >= deadline:
                reason = 'TIMEOUT'
            # Raise outside the handlers so raw exceptions are not chained.
            raise CallbackError(reason)
        return answer
