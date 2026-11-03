"""MCP Events registration on the existing authenticated FastMCP endpoint.

Subscription validation, persistence, and callback delivery belong to the injected
service. This adapter adds protocol dispatch and authenticated account attribution.
"""

import json

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp.types import RequestParams
from pydantic import BaseModel, ConfigDict


EVENT_METHODS = ("events/list", "events/subscribe", "events/unsubscribe")


class EventParams(RequestParams):
    # The owning event service validates each method's complete input schema.
    # Retain extension fields: the SDK's core RequestParams only knows _meta.
    model_config = ConfigDict(extra="allow")


def _with_events(result):
    if result is None:
        return None
    wire = (result.model_dump(by_alias=True, mode="json", exclude_none=True)
            if isinstance(result, BaseModel) else dict(result))
    wire["capabilities"] = {**wire["capabilities"], "events": {}}
    return wire


class EventCapabilities(Middleware):
    async def on_initialize(self, context, call_next):
        # Add the draft capability after the SDK's core wire-schema sieve,
        # which otherwise removes unknown capabilities even from a dict.
        return _with_events(await call_next(context))

    async def on_discover(self, context, call_next):
        return _with_events(await call_next(context))


class MailboxEvents(ServerExtension):
    identifier = "io.github.cf-fe/task-mailbox-events"

    def __init__(self, service, allowed_login):
        self.service = service
        self.allowed_login = allowed_login.casefold()

    def methods(self):
        return tuple(MethodBinding(method, EventParams, self.handle)
                     for method in EVENT_METHODS)

    async def handle(self, context, params):
        token = get_access_token()
        claims = token.claims if token else None
        login = claims.get("login") if isinstance(claims, dict) else None
        subject = token.subject if token else None
        if (not isinstance(login, str) or login.casefold() != self.allowed_login
                or not isinstance(subject, str) or not subject.strip()):
            raise MCPError(code=-32001, message="AUTHORIZATION_DENIED: this account cannot access mailbox events")
        principal = json.dumps({"login": login.casefold(), "subject": subject},
                               sort_keys=True, separators=(",", ":"))
        payload = params.model_dump(by_alias=True, mode="json", exclude_unset=True)
        payload.pop("_meta", None)
        return await self.service.handle(context.method, principal, payload)


def install_events(server, service, allowed_login):
    """Register all event methods before serving; does not start callbacks.

    ``service.handle(method, principal, params)`` is asynchronous. ``principal``
    is canonical JSON containing the authenticated case-folded GitHub login and
    token subject. Caller-supplied sender/recipient aliases are never identities.
    """
    if (not isinstance(allowed_login, str) or not allowed_login.strip()
            or not callable(getattr(service, "handle", None))):
        raise ValueError("an event service and allowed GitHub account are required")
    server.add_extension(MailboxEvents(service, allowed_login))
    server.add_middleware(EventCapabilities())
