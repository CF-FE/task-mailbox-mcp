"""Offline callback boundary tests; no external DNS, HTTP, TLS, or credentials."""

import io
import http.client
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

import callback_http
from callback_http import CallbackError, PinnedHTTPSPoster


HOST = 'callback.example.com'
URL = 'https://' + HOST + '/events?opaque=secret-value'


def addresses(*ips):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM,
             socket.IPPROTO_TCP, '', (ip, 443, 0, 0) if ':' in ip else (ip, 443))
            for ip in ips]


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.shutdown_event = threading.Event()

    def settimeout(self, value):
        self.timeouts.append(value)

    def shutdown(self, how):
        self.shutdown_event.set()


class FakeResponse:
    def __init__(self, status=200, body=b'ok'):
        self.status = status
        self.body = io.BytesIO(body)
        self.read_sizes = []

    def read1(self, amount):
        self.read_sizes.append(amount)
        return self.body.read(amount)

    def isclosed(self):
        return self.body.closed

    def close(self):
        self.body.close()


class FakeConnection:
    def __init__(self, host, port=443, *, timeout, context, status=200, body=b'ok'):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.response = FakeResponse(status, body)
        self.sock = None
        self.requests = []
        self.closed = False

    def connect(self):
        self.sock = self._create_connection((self.host, self.port), self.timeout)

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class PosterTests(unittest.TestCase):
    def setUp(self):
        self.connections, self.dials = [], []

        def factory(host, **kwargs):
            conn = FakeConnection(host, **kwargs)
            self.connections.append(conn)
            return conn

        self.factory = factory

    def poster(self, resolver=None, **options):
        return PinnedHTTPSPoster([HOST], resolver=resolver or (lambda *a, **kw: addresses('8.8.8.8')),
                                 connection_factory=self.factory, **options)

    def dial(self, address, timeout, source_address=None):
        self.dials.append((address, timeout, source_address))
        return FakeSocket()

    def assert_reason(self, reason, callback):
        with self.assertRaises(CallbackError) as caught:
            callback()
        self.assertEqual(reason, caught.exception.reason)
        self.assertEqual(reason, str(caught.exception))
        self.assertNotIn('secret-value', repr(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def test_empty_allowlist_rejects_before_resolver_or_connection(self):
        def forbidden(*args, **kwargs):
            self.fail('rejected host must not reach DNS or a connection')
        self.assert_reason('HOST_NOT_ALLOWED', lambda: PinnedHTTPSPoster(
            resolver=forbidden, connection_factory=forbidden).post(URL, b'{}', {}))

    def test_unlisted_subdomain_and_suffix_are_rejected(self):
        for host in ('other.' + HOST, HOST + '.evil.example'):
            with self.subTest(host=host):
                self.assert_reason('HOST_NOT_ALLOWED', lambda: self.poster().post('https://' + host, b'', {}))

    def test_url_boundary_rejects_credentials_ports_literals_unicode_and_controls(self):
        for url in ('http://' + HOST, 'https://user:secret-value@' + HOST,
                    'https://' + HOST + ':444/x', 'https://' + HOST + '#secret-value',
                    'https://' + HOST + '\n/x', 'https://127.0.0.1/x', 'https://[::1]/x',
                    'https://c\u00e4llback.example.com/x', 'https://' + HOST + '/x' * 3000):
            with self.subTest(url=url):
                self.assert_reason('INVALID_URL', lambda: self.poster().post(url, b'', {}))

    def test_mixed_dns_answers_reject_entire_resolution(self):
        for ip in ('127.0.0.1', '10.0.0.2', '169.254.169.254', '192.0.2.1',
                   '224.0.0.1', '::1', 'fe80::1', '::ffff:127.0.0.1'):
            with self.subTest(ip=ip):
                self.assert_reason('ADDRESS_NOT_PUBLIC', lambda: self.poster(
                    resolver=lambda *a, **kw: addresses('8.8.8.8', ip)).post(URL, b'', {}))
        self.assertEqual([], self.connections)

    def test_pins_checked_ip_preserves_host_path_and_tls_verification(self):
        with patch.object(callback_http.socket, 'create_connection', self.dial):
            answer = self.poster().post(URL, b'{}', {'Content-Type': 'application/json'})
        self.assertEqual((200, b'ok'), answer)
        conn = self.connections[0]
        self.assertEqual(HOST, conn.host)
        self.assertEqual(443, conn.port)
        self.assertTrue(conn.context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, conn.context.verify_mode)
        self.assertEqual(('8.8.8.8', 443), self.dials[0][0])
        self.assertEqual(('POST', '/events?opaque=secret-value', b'{}'), conn.requests[0][:3])
        self.assertTrue(conn.closed)

    def test_every_post_resolves_again_but_dial_uses_only_checked_ip(self):
        calls = []
        def resolver(host, port, **kwargs):
            calls.append(host)
            return addresses('8.8.8.8' if len(calls) == 1 else '1.1.1.1')
        poster = self.poster(resolver)
        with patch.object(callback_http.socket, 'create_connection', self.dial):
            poster.post(URL, b'first', {})
            poster.post(URL, b'second', {})
        self.assertEqual([HOST, HOST], calls)
        self.assertEqual([('8.8.8.8', 443), ('1.1.1.1', 443)], [d[0] for d in self.dials])

    def test_redirect_is_returned_without_following_or_resolving_target(self):
        def factory(host, **kwargs):
            conn = FakeConnection(host, status=302, body=b'moved', **kwargs)
            self.connections.append(conn)
            return conn
        poster = PinnedHTTPSPoster([HOST], resolver=lambda *a, **kw: addresses('8.8.8.8'),
                                  connection_factory=factory)
        with patch.object(callback_http.socket, 'create_connection', self.dial):
            self.assertEqual((302, b'moved'), poster.post(URL, b'', {}))
        self.assertEqual(1, len(self.connections))
        self.assertEqual(1, len(self.dials))

    def test_response_limit_accepts_exact_length_and_rejects_one_extra_byte(self):
        for size, reason in ((65536, None), (65537, 'RESPONSE_TOO_LARGE')):
            def factory(host, **kwargs):
                conn = FakeConnection(host, body=b'x' * size, **kwargs)
                self.connections.append(conn)
                return conn
            poster = PinnedHTTPSPoster([HOST], resolver=lambda *a, **kw: addresses('8.8.8.8'),
                                      connection_factory=factory)
            with patch.object(callback_http.socket, 'create_connection', self.dial):
                if reason:
                    self.assert_reason(reason, lambda: poster.post(URL, b'', {}))
                else:
                    self.assertEqual(size, len(poster.post(URL, b'', {})[1]))
            self.assertTrue(self.connections[-1].closed)

    def test_expected_network_and_tls_errors_have_safe_reasons(self):
        for error, reason in ((socket.gaierror('secret-value'), 'DNS_FAILED'),
                              (TimeoutError('secret-value'), 'TIMEOUT'),
                              (ssl.SSLError('secret-value /private/cert'), 'TLS_FAILED'),
                              (ConnectionError('secret-value'), 'CONNECT_FAILED')):
            def factory(host, **kwargs):
                raise error
            poster = PinnedHTTPSPoster([HOST], resolver=lambda *a, **kw: addresses('8.8.8.8'),
                                      connection_factory=factory)
            if isinstance(error, socket.gaierror):
                poster = self.poster(resolver=lambda *a, **kw: (_ for _ in ()).throw(error))
            with self.subTest(reason=reason):
                self.assert_reason(reason, lambda: poster.post(URL, b'', {}))

    def test_unknown_programming_error_propagates(self):
        def broken(host, **kwargs):
            raise RuntimeError('programming bug')
        poster = PinnedHTTPSPoster([HOST], resolver=lambda *a, **kw: addresses('8.8.8.8'),
                                  connection_factory=broken)
        with self.assertRaisesRegex(RuntimeError, 'programming bug'):
            poster.post(URL, b'', {})

    def test_two_stuck_dns_threads_hold_slots_and_new_calls_do_not_spawn_more(self):
        gate, entered = threading.Event(), threading.Condition()
        active = 0
        calls = 0
        def blocked(*args, **kwargs):
            nonlocal active, calls
            with entered:
                active += 1
                calls += 1
                entered.notify_all()
            try:
                gate.wait(2)
                return addresses('8.8.8.8')
            finally:
                with entered:
                    active -= 1
                    entered.notify_all()
        poster = self.poster(blocked, timeout=0.08)
        failures = []
        def invoke():
            try:
                poster.post(URL, b'', {})
            except CallbackError as error:
                failures.append(error.reason)
        threads = [threading.Thread(target=invoke) for _ in range(2)]
        try:
            for thread in threads:
                thread.start()
            with entered:
                self.assertTrue(entered.wait_for(lambda: active == 2, timeout=1))
            for thread in threads:
                thread.join(1)
                self.assertFalse(thread.is_alive())
            self.assertEqual(['TIMEOUT', 'TIMEOUT'], sorted(failures))
            start = time.monotonic()
            self.assert_reason('TIMEOUT', lambda: poster.post(URL, b'', {}))
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertEqual(2, calls)
        finally:
            gate.set()
            for thread in threads:
                thread.join(1)
            with entered:
                self.assertTrue(entered.wait_for(lambda: active == 0, timeout=1))

    def test_shared_deadline_reaches_socket_and_response_read(self):
        def resolver(*args, **kwargs):
            time.sleep(0.025)
            return addresses('8.8.8.8')
        poster = self.poster(resolver, timeout=0.2)
        with patch.object(callback_http.socket, 'create_connection', self.dial):
            poster.post(URL, b'', {})
        self.assertLess(self.connections[0].timeout, 0.2)
        self.assertTrue(all(0 < timeout < 0.2 for timeout in self.connections[0].sock.timeouts))

    def test_real_https_connection_preserves_sni_with_connection_close_response(self):
        class WireSocket(FakeSocket):
            def __init__(self):
                super().__init__()
                self.data = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                self.sent = []
            def setsockopt(self, *args):
                pass
            def makefile(self, *args):
                return self.data
            def sendall(self, data):
                self.sent.append(data)
            def close(self):
                pass  # The response file retains the real socket's descriptor.
        class Context:
            verify_mode = ssl.CERT_REQUIRED
            check_hostname = True
            def __init__(self):
                self.names = []
            def wrap_socket(self, sock, *, server_hostname):
                self.names.append(server_hostname)
                return sock
        wire, context = WireSocket(), Context()
        poster = PinnedHTTPSPoster([HOST], resolver=lambda *a, **kw: addresses('8.8.8.8'))
        with patch.object(callback_http.ssl, 'create_default_context', return_value=context), \
                patch.object(callback_http.socket, 'create_connection', return_value=wire) as dial:
            self.assertEqual((200, b'ok'), poster.post(URL, b'{}', {}))
        self.assertEqual([HOST], context.names)
        self.assertEqual(('8.8.8.8', 443), dial.call_args.args[0])
        self.assertIn(('Host: ' + HOST).encode(), b''.join(wire.sent))

    def test_http_error_is_sanitized_and_header_injection_is_rejected(self):
        def factory(host, **kwargs):
            conn = FakeConnection(host, **kwargs)
            def malformed():
                raise http.client.BadStatusLine('secret-value')
            conn.getresponse = malformed
            return conn
        poster = PinnedHTTPSPoster([HOST], resolver=lambda *a, **kw: addresses('8.8.8.8'), connection_factory=factory)
        with patch.object(callback_http.socket, 'create_connection', self.dial):
            self.assert_reason('HTTP_FAILED', lambda: poster.post(URL, b'', {}))
        for headers in ({'X-Secret': 'value\r\nHost: internal'}, {'Host': 'internal'}, {'Content-Length': '0'}):
            self.assert_reason('REQUEST_INVALID', lambda: poster.post(URL, b'', headers))

    def test_slow_response_exhausts_original_deadline_and_closes_resources(self):
        def factory(host, **kwargs):
            conn = FakeConnection(host, **kwargs)
            def slow_read(amount):
                time.sleep(0.05)
                return b'x'
            conn.response.read1 = slow_read
            self.connections.append(conn)
            return conn
        poster = PinnedHTTPSPoster([HOST], timeout=0.03,
            resolver=lambda *a, **kw: addresses('8.8.8.8'), connection_factory=factory)
        with patch.object(callback_http.socket, 'create_connection', self.dial):
            self.assert_reason('TIMEOUT', lambda: poster.post(URL, b'', {}))
        self.assertTrue(self.connections[0].closed)
        self.assertTrue(self.connections[0].response.isclosed())

    def test_invalid_configuration_is_rejected_without_network(self):
        for options in ({'allowed_hosts': ['*.example.com']}, {'allowed_hosts': ['127.0.0.1']},
                        {'allowed_hosts': ['c\u00e4llback.example.com']}, {'timeout': 0},
                        {'timeout': float('nan')}, {'max_response_bytes': 65537}):
            with self.subTest(options=options):
                self.assert_reason('INVALID_CONFIGURATION', lambda: PinnedHTTPSPoster(**options))

    def test_watchdog_interrupts_slow_headers_and_body_with_one_total_deadline(self):
        for stage in ('headers', 'body'):
            def factory(host, **kwargs):
                conn = FakeConnection(host, **kwargs)
                def blocked():
                    conn.sock.shutdown_event.wait(1)
                    raise ConnectionError('secret-value from a slow callback')
                if stage == 'headers':
                    conn.getresponse = blocked
                else:
                    conn.response.read1 = lambda amount: blocked()
                self.connections.append(conn)
                return conn
            poster = PinnedHTTPSPoster([HOST], timeout=0.08,
                resolver=lambda *a, **kw: addresses('8.8.8.8'), connection_factory=factory)
            with self.subTest(stage=stage), patch.object(callback_http.socket, 'create_connection', self.dial):
                start = time.monotonic()
                self.assert_reason('TIMEOUT', lambda: poster.post(URL, b'', {}))
                self.assertLess(time.monotonic() - start, 0.7)
                self.assertTrue(self.connections[-1].closed)
                self.assertTrue(self.connections[-1].sock.shutdown_event.is_set())


if __name__ == '__main__':
    unittest.main()
