"""Bounded, outbound-only mailbox collection. No task or model execution."""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import ssl
import stat
import sqlite3
import time
from urllib.parse import urlsplit


class AuthRequired(RuntimeError):
    """The dedicated bridge login has not been completed."""


class AuthFailed(RuntimeError):
    """Existing bridge authorization was rejected; interactive login is required."""


COLLECTOR_CALL_TIMEOUT = 75
COLLECTOR_ROUND_TIMEOUT = 155


class BudgetExhausted(RuntimeError):
    pass


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    page_size: int = 25
    pages: int = 4
    rpc: int = 10
    call_timeout: float = COLLECTOR_CALL_TIMEOUT
    round_timeout: float = COLLECTOR_ROUND_TIMEOUT

    def __post_init__(self):
        for name, maximum in (("page_size", 100), ("pages", 20), ("rpc", 100)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"invalid {name} budget")
        for name in ("call_timeout", "round_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError(f"invalid {name} budget")


def _hash(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise ProtocolError("INVALID_JSON") from error
    if len(raw) > 1048576:
        raise ProtocolError("RECORD_TOO_LARGE")
    return hashlib.sha256(raw).hexdigest()


def _permission_denied(text):
    return isinstance(text, str) and (text == "AUTHORIZATION_DENIED" or text.startswith("AUTHORIZATION_DENIED:"))


def _error_chain(error):
    pending, seen = [error], set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        pending.extend(child for child in (getattr(item, "__cause__", None),
                       getattr(item, "__context__", None)) if child is not None)
        pending.extend(getattr(item, "exceptions", ()))


def _auth_error(error):
    for item in _error_chain(error):
        if isinstance(item, (AuthRequired, AuthFailed)):
            return "AUTH_REQUIRED" if isinstance(item, AuthRequired) else "AUTH_FAILED"
        if _permission_denied(str(item)):
            return "AUTH_FAILED"
        if (getattr(item, "status_code", None) in (401, 403)
                or getattr(getattr(item, "response", None), "status_code", None) in (401, 403)):
            return "AUTH_FAILED"
    return None


AUTH_FAILURE_CAUSES = frozenset({"AUTHORIZATION_DENIED", "CLIENT_REGISTRATION_EXPIRED",
    "DISCOVERY_METADATA_MISSING", "REFRESH_REJECTED", "INVALID_REFRESH_RESPONSE",
    "INTERACTIVE_AUTH_DISABLED", "AUTHORIZATION_DESTINATION_REJECTED", "BROWSER_OPEN_FAILED",
    "AUTH_REJECTED"})
PROTOCOL_CAUSES = frozenset({"INVALID_JSON", "RECORD_TOO_LARGE", "TOOL_ERROR", "INVALID_TOOL_RESULT",
    "JOB_IDENTITY_MISMATCH", "JOB_EVENT_HASH_MISMATCH", "CANCEL_NOT_CONFIRMED",
    "INVALID_RESULT_VERSION", "RESULT_IDENTITY_MISMATCH", "RESULT_EVENT_MISMATCH",
    "RESULT_EVENT_MISSING", "INVALID_EVENT_PAGE"})
FAILURE_STAGES = frozenset({"CLIENT_CREATE", "CLIENT_OPEN", "CLIENT_CLOSE", "ROUND", "INBOX",
    "OUTBOX", "GET_JOB", "GET_RESULT", "LIST_EVENTS", "PUBLISH_RESULT", "REPORT_RECEIPT"})
ERROR_TYPES = frozenset({"AuthRequired", "AuthFailed", "ProtocolError", "BudgetExhausted",
    "TimeoutError", "OSError", "ValueError", "RuntimeError", "ExceptionGroup", "BaseExceptionGroup",
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError", "ConnectionAbortedError",
    "PermissionError", "FileNotFoundError", "TypeError", "KeyError", "DatabaseError",
    "OperationalError", "IntegrityError", "JSONDecodeError", "ReadTimeout", "WriteTimeout",
    "ConnectTimeout", "PoolTimeout", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError",
    "LocalProtocolError", "McpError", "ToolError", "ClientError"})


TRANSPORT_ERROR_NAMES = frozenset({"TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "NetworkError", "ConnectError", "ReadError", "WriteError", "CloseError", "ProxyError",
    "RemoteProtocolError", "LocalProtocolError", "EndOfStream", "BrokenResourceError", "ClosedResourceError"})


def _is_transport_failure(error):
    """Only recognized transport failures can yield to the other direction."""
    chain = list(_error_chain(error))
    if any(isinstance(item, (sqlite3.Error, TypeError, PermissionError, FileNotFoundError)) for item in chain):
        return False
    for item in chain:
        children = getattr(item, "exceptions", ())
        if children and not all(_is_transport_failure(child) for child in children):
            return False
    return any(isinstance(item, OSError) or (
        type(item).__module__.split(".", 1)[0] in {"httpx", "httpx2", "httpcore", "httpcore2", "anyio"}
        and type(item).__name__ in TRANSPORT_ERROR_NAMES) for item in chain)


def _failure(error, stage):
    """Persist only fixed codes; exception messages and external values are data."""
    state = _auth_error(error)
    chain = list(_error_chain(error))
    if state is None:
        state = "PROTOCOL_FAILED" if any(isinstance(item, (ProtocolError, ValueError))
                                           for item in chain) else "NETWORK_FAILED"
    cause = "UNKNOWN_CAUSE"
    for item in chain:
        code = item.args[0] if len(item.args) == 1 and isinstance(item.args[0], str) else None
        if ((state == "AUTH_REQUIRED" and isinstance(item, AuthRequired) and code == "LOGIN_REQUIRED")
                or (state == "AUTH_FAILED" and isinstance(item, AuthFailed) and code in AUTH_FAILURE_CAUSES)
                or (state == "PROTOCOL_FAILED" and isinstance(item, ProtocolError) and code in PROTOCOL_CAUSES)
                or (state == "NETWORK_FAILED" and isinstance(item, OSError) and code == "REFRESH_TEMPORARILY_UNAVAILABLE")):
            cause = code
            break
        if state == "AUTH_FAILED" and _permission_denied(str(item)):
            cause = "AUTHORIZATION_DENIED"
            break
        status = getattr(item, "status_code", None) or getattr(getattr(item, "response", None), "status_code", None)
        if state == "AUTH_FAILED" and status in (401, 403):
            cause = "HTTP_UNAUTHORIZED" if status == 401 else "HTTP_FORBIDDEN"
            break
    safe_stage = stage if stage in FAILURE_STAGES else "UNKNOWN"
    kind = type(error).__name__
    safe_type = kind if kind in ERROR_TYPES else "UNKNOWN"
    return state, f"{cause} stage:{safe_stage} type:{safe_type}"


def _client_open_detail(error, elapsed_ms, attempt):
    """Return only bounded stage, exception and status metadata."""
    chain = list(_error_chain(error))
    known = ERROR_TYPES | TRANSPORT_ERROR_NAMES | {"gaierror", "SSLError", "SSLCertVerificationError",
                                                  "HTTPStatusError", "MCPError"}
    kinds = list(dict.fromkeys(type(item).__name__ if type(item).__name__ in known else "UNKNOWN"
                               for item in chain))
    category = "UNKNOWN"
    if any(isinstance(item, socket.gaierror) for item in chain):
        category = "DNS"
    elif any(isinstance(item, ssl.SSLError) for item in chain):
        category = "TLS"
    elif any(isinstance(item, TimeoutError) or type(item).__name__ in {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"} for item in chain):
        category = "TIMEOUT"
    elif any(type(item).__name__ in TRANSPORT_ERROR_NAMES for item in chain):
        category = "HTTP_TRANSPORT"
    elif "HTTPStatusError" in kinds:
        category = "HTTP_STATUS"
    elif "MCPError" in kinds or "McpError" in kinds:
        category = "MCP"
    phase, duration, status = attempt or ("NO_HTTP", 0, None)
    http_status = f" status:{status}" if status is not None else ""
    return (f"category:{category} phase:{phase} phase_ms:{duration} "
            f"open_ms:{elapsed_ms}{http_status} chain:" + "-".join(kinds[:5]))


def _data(response):
    # FastMCP CallToolResult uses is_error/content; the MCP wire shape uses
    # isError/content. Only the server's exact machine-code prefix is terminal.
    is_error = (response.get("is_error", response.get("isError", False)) if isinstance(response, dict)
                else getattr(response, "is_error", getattr(response, "isError", False)))
    if is_error is True:
        content = response.get("content", []) if isinstance(response, dict) else getattr(response, "content", [])
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if kind == "text" and _permission_denied(text):
                raise AuthFailed("AUTHORIZATION_DENIED")
        raise ProtocolError("TOOL_ERROR")
    if isinstance(response, dict):
        value = response
    else:
        value = getattr(response, "structured_content", None)
        if value is None:
            value = getattr(response, "data", None)
    if not isinstance(value, dict):
        raise ProtocolError("INVALID_TOOL_RESULT")
    if _permission_denied(value.get("error")):
        raise AuthFailed("AUTHORIZATION_DENIED")
    if "error" in value:
        raise ProtocolError("TOOL_ERROR")
    return value


class Budget:
    def __init__(self, call_tool, limits):
        self.call_tool, self.limits = call_tool, limits
        self.deadline, self.calls = time.monotonic() + limits.round_timeout, 0
        self.phase_limit = limits.rpc
        self.stage, self.reports_sent = "ROUND", 0

    def reserve_for_next(self, calls=0):
        # Both directions share this counter and deadline. A phase can yield
        # early without granting the other phase a fresh global budget.
        self.phase_limit = self.limits.rpc - calls

    def check(self):
        remaining = self.deadline - time.monotonic()
        if self.calls >= min(self.limits.rpc, self.phase_limit) or remaining <= 0:
            raise BudgetExhausted()
        return remaining

    async def call(self, name, arguments):
        self.stage = name.upper() if name.upper() in FAILURE_STAGES else "ROUND"
        remaining = self.check()
        self.calls += 1
        return _data(await asyncio.wait_for(self.call_tool(name, arguments),
                                          min(self.limits.call_timeout, remaining)))


async def _handle_pending(store, budget, verified, port=None):
    for event in store.pending_events(limit=100):
        budget.check()
        kind, data = event["event_type"], event["data"]
        if kind not in {"JOB_PERSISTED", "CANCEL_REQUESTED", "RESULT_REPORTED", "RESULT_READ_ACKNOWLEDGED"}:
            store.finish_event(event["event_id"], "HELD", "UNKNOWN_EVENT_TYPE")
            continue
        try:
            job = await budget.call("get_job", {"job_id": event["job_id"]})
            if job.get("job_id") != event["job_id"] or job.get("payload_hash") != _hash(job.get("payload")):
                raise ProtocolError("JOB_IDENTITY_MISMATCH")
            if kind == "JOB_PERSISTED" and data.get("payload_hash") != job["payload_hash"]:
                raise ProtocolError("JOB_EVENT_HASH_MISMATCH")
            if kind == "CANCEL_REQUESTED" and job.get("cancel_requested") is not True:
                raise ProtocolError("CANCEL_NOT_CONFIRMED")
            record = {"event": event, "job": job}
            if kind in {"RESULT_REPORTED", "RESULT_READ_ACKNOWLEDGED"}:
                version = data.get("version")
                if type(version) is not int or version < 1:
                    raise ProtocolError("INVALID_RESULT_VERSION")
                result = await budget.call("get_result", {"job_id": event["job_id"], "version": version})
                if (result.get("job_id") != event["job_id"] or type(result.get("version")) is not int or result["version"] != version
                        or result.get("result_hash") != data.get("result_hash")
                        or not isinstance(result.get("result"), dict)
                        or result["result_hash"] != _hash(result["result"])
                        or result.get("status") not in {"REPORTED_COMPLETED", "REPORTED_FAILED", "UNKNOWN"}
                        or type(job.get("latest_result_version")) is not int
                        or job["latest_result_version"] < version):
                    raise ProtocolError("RESULT_IDENTITY_MISMATCH")
                if kind == "RESULT_REPORTED" and (result.get("event_id") != event["event_id"]
                                                   or result.get("status") != data.get("status")):
                    raise ProtocolError("RESULT_EVENT_MISMATCH")
                if not isinstance(result.get("event_id"), str) or not result["event_id"]:
                    raise ProtocolError("RESULT_EVENT_MISSING")
                record["result"] = result
            # The ledger owns payload/schema validation and immutable identity.
            store.record_job(job, proof_hash=port.capture_proof(job) if port else None)
            store.finish_event(event["event_id"])
            verified.append(record)
        except (ProtocolError, ValueError) as error:
            if _auth_error(error) is not None:
                raise
            if isinstance(error, ProtocolError):
                code = error.args[0] if len(error.args) == 1 and isinstance(error.args[0], str) else None
                reason = code if code in PROTOCOL_CAUSES else "UNKNOWN_PROTOCOL_ERROR"
            else:
                reason = "LOCAL_RECORD_REJECTED"
            store.finish_event(event["event_id"], "HELD", reason)


async def flush_reports(store, call_tool, *, budget=None, limits=Limits()):
    """Retransmit saved reports with their original event IDs; never creates work."""
    budget = budget or Budget(call_tool, limits)
    sent = 0
    for report in store.pending_reports(limit=min(2, limits.rpc)):
        receipt = await budget.call("publish_result", {key: report[key]
            for key in ("job_id", "event_id", "status", "result")})
        # Store verifies the full identity, hash and positive version atomically.
        budget.stage = "REPORT_RECEIPT"
        store.mark_report_sent(report["event_id"], receipt)
        sent += 1
        budget.reports_sent += 1
    return {"sent": sent, "calls": budget.calls}


async def collect_once(store, call_tool, *, limits=Limits(), first_lane="inbox", port=None):
    """Give receive/upload independent chances inside one shared round budget."""
    if first_lane not in ("inbox", "outbox"):
        raise ValueError("invalid first collection direction")
    budget, verified = Budget(call_tool, limits), []
    received = 0
    state, detail = "OK", "ROUND_COMPLETE"
    lanes = ("inbox", "outbox") if first_lane == "inbox" else ("outbox", "inbox")
    for index, lane in enumerate(lanes):
        later_work = index == 0 and (lanes[1] == "inbox" or bool(store.pending_reports(limit=1)))
        # A result reconciliation needs get_job + get_result atomically within
        # the round. Do not split a two-RPC budget into two unusable slices.
        # Time is never halved: the priority direction retains the one global
        # deadline, and the worker rotates priority across rounds.
        budget.reserve_for_next(int(later_work and limits.rpc >= 3))
        budget.stage = lane.upper()
        try:
            if lane == "outbox":
                await flush_reports(store, call_tool, budget=budget, limits=limits)
                continue
            await _handle_pending(store, budget, verified, port)
            for _ in range(limits.pages):
                page = await budget.call("list_events", {"cursor": store.cursor(), "limit": limits.page_size})
                if not isinstance(page.get("events"), list) or len(page["events"]) > limits.page_size:
                    raise ProtocolError("INVALID_EVENT_PAGE")
                received += store.ingest_page(page)["received"]
                await _handle_pending(store, budget, verified, port)
                if not page["has_more"]:
                    break
            else:
                if page["has_more"] and state == "OK":
                    state, detail = "BUDGET_EXHAUSTED", "PAGE_BUDGET"
            # Older ledgers retain only routing. Reconcile one already known,
            # explicitly attested original inside the existing inbox RPC budget.
            if port is not None and (original := port.next_missing()):
                job = await budget.call("get_job", {"job_id": original})
                if job.get("job_id") != original:
                    raise ProtocolError("JOB_IDENTITY_MISMATCH")
                proof_hash = port.capture_proof(job)
                if proof_hash is not None:
                    store.record_job(job, proof_hash=proof_hash)
        except BudgetExhausted:
            if state == "OK":
                state, detail = "BUDGET_EXHAUSTED", "RPC_OR_TIME_BUDGET"
        except Exception as error:
            failure_state, failure_detail = _failure(error, budget.stage)
            if failure_state != "NETWORK_FAILED" or not _is_transport_failure(error):
                state, detail = failure_state, failure_detail
                break
            if state != "NETWORK_FAILED":
                state, detail = failure_state, failure_detail
            # Only a recoverable transport failure yields to the other lane.
            # The failed operation is not retried within this round.
    store.set_health("collector", state, detail)
    if state == "OK":
        store.set_health("collector_last_success", "OK", "ROUND_COMPLETE")
    return {"state": state, "calls": budget.calls, "received": received,
            "sent": budget.reports_sent, "cursor": store.cursor(), "verified": verified,
            "pending_events": len(store.pending_events(limit=1000)),
            "pending_reports": len(store.pending_reports(limit=1000))}


async def run_collector(store, client_factory, stop_event, *, limits=Limits(), interval=30, port=None):
    """Run mailbox and optional monitoring with independent request budgets."""
    if __package__:
        from .observability import SETTINGS_NAME, run_monitor
    else:
        from observability import SETTINGS_NAME, run_monitor

    path = getattr(store, 'path', None)
    state_dir = Path(path).parent if path is not None else None
    if state_dir is None or not os.path.lexists(state_dir / SETTINGS_NAME):
        return await _run_mailbox(store, client_factory, stop_event, limits=limits, interval=interval, port=port)
    monitor = asyncio.create_task(run_monitor(state_dir, stop_event))
    try:
        await _run_mailbox(store, client_factory, stop_event, limits=limits, interval=interval, port=port)
        if not stop_event.is_set():
            # Mailbox authorization/protocol stop leaves the independent source alive.
            await stop_event.wait()
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


async def _run_mailbox(store, client_factory, stop_event, *, limits, interval, port):
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not math.isfinite(interval) or not 1 <= interval <= 3600:
        raise ValueError("invalid polling interval")
    failures, first_lane = 0, "inbox"
    while not stop_event.is_set():
        stage, result = "CLIENT_CREATE", None
        open_started = None
        try:
            client = await client_factory()
            stage = "CLIENT_OPEN"
            open_started = time.monotonic()
            async with client:
                stage = "ROUND"
                result = await collect_once(store, client.call_tool, limits=limits, first_lane=first_lane, port=port)
                stage = "CLIENT_CLOSE"
            state = result["state"]
        except Exception as error:
            if result is not None and result["state"] in {"AUTH_REQUIRED", "AUTH_FAILED", "PROTOCOL_FAILED"}:
                # A later close failure must not turn an authentication stop
                # into an automatically retried network error.
                state = result["state"]
            else:
                state, detail = _failure(error, stage)
                store.set_health("collector", state, detail)
                if stage == "CLIENT_OPEN":
                    attempt = getattr(client, "_bridge_open_attempt", None)
                    elapsed = int((time.monotonic() - open_started) * 1000)
                    store.set_health("collector_open_failure", state, _client_open_detail(error, elapsed, attempt))
        # A busy direction can consume the shared time deadline. Alternate
        # priority at every budget size, without adding persistent task state.
        first_lane = "outbox" if first_lane == "inbox" else "inbox"
        if state in {"AUTH_REQUIRED", "AUTH_FAILED", "PROTOCOL_FAILED"}:
            return
        failures = failures + 1 if state == "NETWORK_FAILED" else 0
        delay = (60, 120, 300)[min(failures - 1, 2)] if failures else interval
        try:
            await asyncio.wait_for(stop_event.wait(), delay)
        except asyncio.TimeoutError:
            pass


async def create_client(endpoint, state_directory, *, allow_login=False):
    """Build the independent encrypted OAuth client; only login may authorize."""
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("an explicit HTTPS MCP endpoint is required")
    endpoint = endpoint.rstrip("/")
    directory = Path(state_directory).expanduser()
    key_path, cache_path = directory / "client.key", directory / "oauth"
    if not allow_login and (not key_path.is_file() or not cache_path.is_dir()):
        raise AuthRequired("LOGIN_REQUIRED")
    # Lazy imports keep missing-cache checks and fake tests stdlib-only.
    from cryptography.fernet import Fernet
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from metadata_transport import metadata_client_factory
    from browser_authorization import open_browser_authorization, sanitize_client_logging
    sanitize_client_logging()
    from fastmcp.client.auth import OAuth
    from fastmcp.client.auth.oauth import TokenStorageAdapter
    from key_value.aio.stores.filetree import (FileTreeStore,
        FileTreeV1CollectionSanitizationStrategy, FileTreeV1KeySanitizationStrategy)
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("OAuth directory must not be a symlink")
    os.chmod(directory, 0o700)
    if allow_login and not key_path.exists():
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(Fernet.generate_key())
            handle.flush()
            os.fsync(handle.fileno())
    if key_path.is_symlink() or not stat.S_ISREG(key_path.stat().st_mode):
        raise ValueError("invalid dedicated OAuth key file")
    os.chmod(key_path, 0o600)
    if cache_path.is_symlink():
        raise ValueError("OAuth cache must not be a symlink")
    cache_path.mkdir(mode=0o700, exist_ok=True)

    def secure_cache():
        for base, dirs, files in os.walk(cache_path, followlinks=False):
            os.chmod(base, 0o700)
            for name in dirs + files:
                path = Path(base) / name
                if path.is_symlink():
                    raise ValueError("unsafe OAuth cache entry")
                os.chmod(path, 0o700 if path.is_dir() else 0o600)

    secure_cache()

    class PrivateFileStore(FileTreeStore):
        async def put(self, *args, **kwargs):
            await super().put(*args, **kwargs)
            secure_cache()

    storage = FernetEncryptionWrapper(key_value=PrivateFileStore(data_directory=cache_path,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(cache_path),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(cache_path)),
        fernet=Fernet(key_path.read_bytes()), raise_on_decryption_error=True)
    adapter = TokenStorageAdapter(storage, endpoint)
    metadata_key = hashlib.sha256(endpoint.encode()).hexdigest()
    saved = await storage.get(metadata_key, collection="bridge-discovery")
    if not allow_login:
        tokens, info = await adapter.get_tokens(), await adapter.get_client_info()
        if tokens is None or info is None or saved is None:
            raise AuthRequired("LOGIN_REQUIRED")
        if info.client_secret_expires_at and info.client_secret_expires_at <= time.time():
            raise AuthFailed("CLIENT_REGISTRATION_EXPIRED")

    class BridgeOAuth(OAuth):
        async def _initialize(self):
            await super()._initialize()
            if saved is not None:
                self.context.oauth_metadata = OAuthMetadata.model_validate(saved["oauth"])
                self.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(saved["resource"])
                self.context.auth_server_url = saved["issuer"]

        async def _handle_token_response(self, response):
            await super()._handle_token_response(response)
            context = self.context
            if context.oauth_metadata is None or context.protected_resource_metadata is None:
                raise AuthFailed("DISCOVERY_METADATA_MISSING")
            await storage.put(metadata_key, {"oauth": context.oauth_metadata.model_dump(mode="json"),
                "resource": context.protected_resource_metadata.model_dump(mode="json"),
                "issuer": context.auth_server_url}, collection="bridge-discovery")

        async def _handle_refresh_response(self, response):
            if response.status_code in (429, 500, 502, 503, 504):
                raise OSError("REFRESH_TEMPORARILY_UNAVAILABLE")
            if response.status_code != 200:
                if allow_login:
                    return await super()._handle_refresh_response(response)
                raise AuthFailed("REFRESH_REJECTED")
            if not await super()._handle_refresh_response(response):
                raise AuthFailed("INVALID_REFRESH_RESPONSE")
            return True

        async def _perform_authorization(self):
            if not allow_login:
                raise AuthFailed("INTERACTIVE_AUTH_DISABLED")
            return await super()._perform_authorization()

        async def redirect_handler(self, authorization_url):
            if not allow_login:
                raise AuthFailed("INTERACTIVE_AUTH_DISABLED")
            try:
                open_browser_authorization(endpoint, authorization_url)
            except ValueError as error:
                raise AuthFailed("AUTHORIZATION_DESTINATION_REJECTED") from error
            except OSError as error:
                raise AuthFailed("BROWSER_OPEN_FAILED") from error

        async def callback_handler(self):
            if not allow_login:
                raise AuthFailed("INTERACTIVE_AUTH_DISABLED")
            return await super().callback_handler()

        async def async_auth_flow(self, request):
            # Stop before the SDK handles a challenge: no discovery, DCR, scope
            # expansion or cache clearing is allowed in the worker branch.
            async with aclosing(super().async_auth_flow(request)) as flow:
                response = None
                while True:
                    try:
                        outgoing = await flow.asend(response)
                    except StopAsyncIteration:
                        return
                    response = yield outgoing
                    if not allow_login and response.status_code in (401, 403):
                        raise AuthFailed("AUTH_REJECTED")

    oauth = BridgeOAuth(mcp_url=endpoint, token_storage=storage,
                        client_name="Mac Coordination Bridge", callback_host="127.0.0.1")
    def record_attempt(phase, elapsed_ms, status):
        client._bridge_open_attempt = (phase, elapsed_ms, status)

    token_endpoint = saved["oauth"].get("token_endpoint") if not allow_login and saved else None
    transport = StreamableHttpTransport(endpoint, auth=oauth,
        httpx_client_factory=metadata_client_factory(
            endpoint, observe=record_attempt if not allow_login else None, token_endpoint=token_endpoint))
    # An auto-negotiation probe can expire while its HTTP task is still doing
    # OAuth discovery. The fallback initialize then owns a different request
    # clock, so waiting for browser consent does not pause its 10-second limit.
    # Login uses the supported legacy handshake and one bounded consent window;
    # Normal collector requests retain auto negotiation and its own limit.
    client = Client(transport, timeout=330 if allow_login else COLLECTOR_CALL_TIMEOUT,
                  init_timeout=330 if allow_login else 10,
                  mode='legacy' if allow_login else 'auto')
    return client
