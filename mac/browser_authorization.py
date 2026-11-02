"""Native browser handoff and non-secret SDK logging for the local bridge."""

import logging
from urllib.parse import urlsplit
import webbrowser


def open_browser_authorization(endpoint, authorization_url):
    service, target = urlsplit(endpoint), urlsplit(authorization_url)
    expected_path = service.path.removesuffix('/mcp') + '/authorize'
    if (target.scheme != 'https' or (target.scheme, target.netloc) != (service.scheme, service.netloc)
            or target.path != expected_path or target.username or target.password or target.fragment):
        raise ValueError('AUTHORIZATION_DESTINATION_REJECTED')
    # The browser owns the authorization GET. Do not duplicate it with a
    # headless preflight, and never log its code challenge or state.
    if not webbrowser.open(authorization_url):
        raise OSError('BROWSER_OPEN_FAILED')
    print('Task Mailbox Mac Bridge: complete authorization in the opened browser.', flush=True)


class SafeClientLog(logging.Filter):
    def filter(self, record):
        error_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else None
        record.msg = 'MCP client event: %s%s'
        record.args = (record.levelname, ' (' + error_type + ')' if error_type else '')
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def sanitize_client_logging():
    # This runs only in the dedicated bridge process, after SDK imports.
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and name.startswith(('mcp.client', 'fastmcp.client')):
            if not any(isinstance(item, SafeClientLog) for item in logger.filters):
                logger.addFilter(SafeClientLog())
