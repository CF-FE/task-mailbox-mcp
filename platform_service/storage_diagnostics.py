"""Log fixed classifications at platform storage error boundaries."""

import json
import logging
import re

from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeoutError

LOGGER = logging.getLogger("task_mailbox.storage")


def log_storage_failure(operation, request_id, error, *, next_attempt=None):
    sqlstate = None
    if isinstance(error, DBAPIError):
        driver = error.orig
        candidate = getattr(driver, "sqlstate", None)
        if isinstance(candidate, str) and re.fullmatch(r"[0-9A-Z]{5}", candidate):
            sqlstate = candidate
        if sqlstate is not None:
            category = "database_sqlstate"
        elif type(driver).__name__ == "ConnectionTimeout":
            category = "connection_timeout"
        elif error.connection_invalidated:
            category = "connection_invalidated"
        else:
            category = "driver_error"
    else:
        category = "pool_timeout" if isinstance(error, PoolTimeoutError) else "sqlalchemy_error"
    record = {
        "operation": operation, "request_id": request_id, "category": category, "sqlstate": sqlstate,
    }
    if next_attempt is not None:
        record["next_attempt"] = next_attempt
    LOGGER.warning("%s", json.dumps(record))
