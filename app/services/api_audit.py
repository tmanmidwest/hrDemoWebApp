"""Middleware that audits every authenticated REST API call.

The domain routers already record *semantic* write events (``employee.created``
etc.), but that leaves two blind spots for anyone trying to see what an external
integration is doing:

* **Reads are invisible.** A connector doing a nightly roster sync issues only
  ``GET`` requests, which record nothing — so a full directory pull leaves no
  trace.
* **No access trail.** Even for writes, there's no single "who called what,
  when, from where, and did it succeed" line you can scan.

This middleware closes both. For every request under ``/api/v1`` it writes one
``api.request`` event *after* the response, so it can capture the **status
code** and **duration** alongside the caller. The caller (a
:class:`~app.services.auth.Principal`) is read from ``request.state``, where
:func:`app.services.auth.get_authenticated_principal` stashes it — so the same
event covers API-key and OAuth-client callers, reads and writes alike.

It is a pure-ASGI middleware (not ``BaseHTTPMiddleware``) so it can reliably
read the ``scope["state"]`` the route populated, and wrap ``send`` to observe
the response status.

Design notes:

* Only **authenticated** API calls are logged (a ``Principal`` was resolved).
  The one exception is a *failed* bearer attempt — a request that presented an
  ``Authorization: Bearer`` header but got 401/403 — which is logged as
  ``api.auth_failed`` so a broken integration (expired/typo'd key) is visible
  rather than silent. Session-authenticated UI calls to ``/api/v1/*`` (which
  never set a principal) are deliberately ignored.
* Recording is offloaded to a worker thread; :func:`record_event` does blocking
  DB I/O and must never stall the event loop. It also never raises.
* ``/oauth/token`` is intentionally *not* covered here — token issuance and
  denial are already audited by the token endpoint itself.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.audit import record_event

# Only requests whose path starts with one of these are considered API traffic.
_AUDITED_PREFIXES = ("/api/v1",)


def _outcome_for_status(status_code: int) -> str:
    """Map an HTTP status to an audit outcome."""
    if status_code >= 500:
        return "error"
    if status_code >= 400:
        return "failure"
    return "success"


def _had_bearer(scope: Scope) -> bool:
    """True if the request carried an ``Authorization: Bearer`` header."""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            return value[:7].lower() == b"bearer "
    return False


class ApiAccessAuditMiddleware:
    """Record an audit event for every authenticated ``/api/v1`` request."""

    def __init__(self, app: ASGIApp, prefixes: Iterable[str] = _AUDITED_PREFIXES) -> None:
        self.app = app
        self.prefixes = tuple(prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefixes):
            await self.app(scope, receive, send)
            return

        status_code = 500  # assume the worst until we see the response start
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            # get_authenticated_principal stashes plain actor primitives here.
            caller = (scope.get("state") or {}).get("api_audit")
            await self._record(scope, caller, status_code, duration_ms)

    async def _record(
        self, scope: Scope, caller: dict | None, status_code: int, duration_ms: float
    ) -> None:
        """Persist one audit event for this call (off the event loop)."""
        method = scope.get("method", "?")
        path = scope.get("path", "")
        query = scope.get("query_string", b"").decode("latin-1") or None

        if caller is not None:
            actor = {
                "actor_type": caller["actor_type"],
                "actor_label": caller["actor_label"],
            }
            detail = {
                "method": method,
                "path": path,
                "query": query,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "principal_kind": caller.get("kind"),
                "scopes": caller.get("scopes", []),
            }
            event_type = "api.request"
        elif status_code in (401, 403) and _had_bearer(scope):
            # A bearer token was presented but rejected — a broken integration.
            actor = {"actor_type": "anonymous", "actor_label": None}
            detail = {
                "method": method,
                "path": path,
                "query": query,
                "status_code": status_code,
            }
            event_type = "api.auth_failed"
        else:
            # Unauthenticated non-bearer traffic or a session-auth UI call — not
            # an API integration event, so don't record anything.
            return

        # Build a Request purely so record_event can pull IP / request-id / UA
        # from the headers using its existing helper.
        request = Request(scope)
        await asyncio.to_thread(
            record_event,
            category="api",
            event_type=event_type,
            outcome=_outcome_for_status(status_code),
            target_type="endpoint",
            target_label=f"{method} {path}",
            message=f"{method} {path} -> {status_code}",
            detail=detail,
            request=request,
            **actor,
        )
