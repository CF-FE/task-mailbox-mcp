"""Bounded, outbound-only mailbox collection. No task or model execution."""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit


class AuthRequired(RuntimeError):
    """The dedicated bridge login has not been completed."""


class AuthFailed(RuntimeError):
    """Existing bridge authorization was rejected; interactive login is required."""


class BudgetExhausted(RuntimeError):
    pass


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    page_size: int = 25
    pages: int = 4
    rpc: int = 10
    call_timeout: float = 10
    round_timeout: float = 20

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


def _auth_error(error):
    pending, seen = [error], set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, (AuthRequired, AuthFailed)):
            return "AUTH_REQUIRED" if isinstance(item, AuthRequired) else "AUTH_FAILED"
        if _permission_denied(str(item)):
            return "AUTH_FAILED"
        if (getattr(item, "status_code", None) in (401, 403)
                or getattr(getattr(item, "response", None), "status_code", None) in (401, 403)):
            return "AUTH_FAILED"
        pending.extend(child for child in (getattr(item, "__cause__", None),
                       getattr(item, "__context__", None)) if child is not None)
        pending.extend(getattr(item, "exceptions", ()))
    return None


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

    def check(self):
        remaining = self.deadline - time.monotonic()
        if self.calls >= self.limits.rpc or remaining <= 0:
            raise BudgetExhausted()
        return remaining

    async def call(self, name, arguments):
        remaining = self.check()
        self.calls += 1
        return _data(await asyncio.wait_for(self.call_tool(name, arguments),
                                          min(self.limits.call_timeout, remaining)))


async def _handle_pending(store, budget, verified):
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
            store.record_job(job)
            store.finish_event(event["event_id"])
            verified.append(record)
        except (ProtocolError, ValueError) as error:
            reason = str(error) if isinstance(error, ProtocolError) else "LOCAL_RECORD_REJECTED"
            store.finish_event(event["event_id"], "HELD", reason)


async def flush_reports(store, call_tool, *, budget=None, limits=Limits()):
    """Retransmit saved reports with their original event IDs; never creates work."""
    budget = budget or Budget(call_tool, limits)
    sent = 0
    for report in store.pending_reports(limit=min(2, limits.rpc)):
        receipt = await budget.call("publish_result", {key: report[key]
            for key in ("job_id", "event_id", "status", "result")})
        # Store verifies the full identity, hash and positive version atomically.
        store.mark_report_sent(report["event_id"], receipt)
        sent += 1
    return {"sent": sent, "calls": budget.calls}


async def collect_once(store, call_tool, *, limits=Limits()):
    """Persist complete pages, then reconcile data. Receipt is not execution/ACK."""
    budget, verified = Budget(call_tool, limits), []
    received = sent = 0
    state, detail = "OK", "ROUND_COMPLETE"
    try:
        await _handle_pending(store, budget, verified)
        sent = (await flush_reports(store, call_tool, budget=budget, limits=limits))["sent"]
        for _ in range(limits.pages):
            page = await budget.call("list_events", {"cursor": store.cursor(), "limit": limits.page_size})
            if not isinstance(page.get("events"), list) or len(page["events"]) > limits.page_size:
                raise ProtocolError("INVALID_EVENT_PAGE")
            received += store.ingest_page(page)["received"]
            await _handle_pending(store, budget, verified)
            if not page["has_more"]:
                break
        else:
            if page["has_more"]:
                state, detail = "BUDGET_EXHAUSTED", "PAGE_BUDGET"
    except BudgetExhausted:
        state, detail = "BUDGET_EXHAUSTED", "RPC_OR_TIME_BUDGET"
    except Exception as error:
        state = _auth_error(error) or ("PROTOCOL_FAILED" if isinstance(error, (ProtocolError, ValueError)) else "NETWORK_FAILED")
        detail = {"AUTH_REQUIRED": "LOGIN_REQUIRED", "AUTH_FAILED": "AUTH_REJECTED",
                  "PROTOCOL_FAILED": "RECORD_REJECTED", "NETWORK_FAILED": "TRANSPORT_FAILED"}[state]
    store.set_health("collector", state, detail)
    if state == "OK":
        store.set_health("collector_last_success", "OK", "ROUND_COMPLETE")
    return {"state": state, "calls": budget.calls, "received": received,
            "sent": sent, "cursor": store.cursor(), "verified": verified,
            "pending_events": len(store.pending_events(limit=1000)),
            "pending_reports": len(store.pending_reports(limit=1000))}


async def run_collector(store, client_factory, stop_event, *, limits=Limits(), interval=30):
    """Run bounded rounds. The CLI owns signal handlers and the one-writer lock."""
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not math.isfinite(interval) or not 1 <= interval <= 3600:
        raise ValueError("invalid polling interval")
    failures = 0
    while not stop_event.is_set():
        try:
            client = await client_factory()
            async with client:
                result = await collect_once(store, client.call_tool, limits=limits)
            state = result["state"]
        except Exception as error:
            state = _auth_error(error) or "NETWORK_FAILED"
            store.set_health("collector", state, "CLIENT_UNAVAILABLE")
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
    transport = StreamableHttpTransport(endpoint, auth=oauth,
        httpx_client_factory=metadata_client_factory(endpoint))
    return Client(transport, timeout=10, init_timeout=300 if allow_login else 10)
