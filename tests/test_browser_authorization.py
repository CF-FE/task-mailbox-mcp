import contextlib
import io
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from browser_authorization import open_browser_authorization, sanitize_client_logging


class BrowserAuthorizationTests(unittest.TestCase):
    def test_same_authorization_url_is_handed_to_browser_without_logging_query(self):
        url = 'https://mcp.example.com/mailbox/authorize?state=TEST_STATE&code_challenge=TEST_CHALLENGE'
        output = io.StringIO()
        with patch('browser_authorization.webbrowser.open', return_value=True) as browser:
            with contextlib.redirect_stdout(output):
                open_browser_authorization('https://mcp.example.com/mailbox/mcp', url)
        browser.assert_called_once_with(url)
        self.assertNotIn('TEST_STATE', output.getvalue())
        self.assertNotIn('TEST_CHALLENGE', output.getvalue())

    def test_changed_destination_is_rejected_before_browser(self):
        urls = ('http://mcp.example.com/mailbox/authorize', 'https://other.example.com/mailbox/authorize',
                'https://mcp.example.com/authorize', 'https://user@mcp.example.com/mailbox/authorize')
        with patch('browser_authorization.webbrowser.open') as browser:
            for url in urls:
                with self.assertRaises(ValueError):
                    open_browser_authorization('https://mcp.example.com/mailbox/mcp', url)
        browser.assert_not_called()

    def test_browser_failure_is_explicit_without_retry(self):
        with patch('browser_authorization.webbrowser.open', return_value=False) as browser:
            with self.assertRaises(OSError):
                open_browser_authorization('https://mcp.example.com/mailbox/mcp', 'https://mcp.example.com/mailbox/authorize')
        self.assertEqual(1, browser.call_count)

    def test_sdk_exception_bodies_and_arguments_are_not_rendered(self):
        logger = logging.getLogger('mcp.client.test_bridge_logging')
        original = list(logger.filters)
        self.addCleanup(lambda: setattr(logger, 'filters', original))
        sanitize_client_logging()
        error = ValueError('TEST_ACCESS_TOKEN')
        record = logger.makeRecord(logger.name, logging.ERROR, 'test.py', 1,
                                   'token=%s', ('TEST_CLIENT_SECRET',), (ValueError, error, None))
        self.assertTrue(logger.filter(record))
        rendered = logging.Formatter('%(message)s').format(record)
        self.assertIn('ValueError', rendered)
        self.assertNotIn('TEST_ACCESS_TOKEN', rendered)
        self.assertNotIn('TEST_CLIENT_SECRET', rendered)


if __name__ == '__main__':
    unittest.main()
