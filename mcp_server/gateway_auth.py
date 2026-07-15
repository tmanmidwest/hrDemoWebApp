"""Inbound access control for the MCP server's HTTP transport.

External clients (Claude, Saviynt, another project) must present a bearer
**gateway token** to reach the MCP endpoint. Those tokens are created and revoked
in the app UI (Settings → MCP); the app writes the SHA-256 hashes of the active
ones to ``<data_dir>/mcp_gateway_tokens.json`` on the shared volume. This module
reads that file live on every request and verifies the presented token against it,
so creating or revoking a token in the UI takes effect immediately — no restart,
and this container never needs the database.

``HRMCP_AUTH_TOKEN`` is a static single-token override for a remote MCP host that
can't see the volume.

Behavior:
* No gateway token configured yet   → 503 (safe to deploy before setup).
* Missing / invalid bearer token    → 401.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from mcp_server.config import get_settings

log = logging.getLogger("hrsot-mcp.gateway")

ACTIVE_TOKENS_FILENAME = "mcp_gateway_tokens.json"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _active_hashes() -> list[str]:
    """Active token hashes from the app-synced file, or [] if none/unreadable."""
    path = get_settings().data_dir / ACTIVE_TOKENS_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text() or "[]")
    except (json.JSONDecodeError, OSError):
        return []
    return [e["hash"] for e in data if isinstance(e, dict) and e.get("hash")]


def is_configured() -> bool:
    """True if any inbound token would authenticate (env override or a synced one).

    When False, the endpoint has nothing to check against and answers 503 (the
    operator hasn't generated a gateway token yet) rather than 401.
    """
    if get_settings().auth_token:
        return True
    return bool(_active_hashes())


def verify(provided: str) -> bool:
    """Constant-time check of a presented bearer against the active tokens."""
    if not provided:
        return False
    override = get_settings().auth_token
    if override and hmac.compare_digest(provided, override):
        return True
    presented_hash = _hash(provided)
    ok = False
    # Compare against every active hash without short-circuiting, to keep timing
    # independent of how many tokens exist or which one matched.
    for h in _active_hashes():
        if hmac.compare_digest(presented_hash, h):
            ok = True
    return ok


async def _send_json(
    send: Any, status: int, body: bytes, extra_headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    headers = [(b"content-type", b"application/json")]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class GatewayAuthMiddleware:
    """ASGI middleware enforcing inbound bearer auth for the HTTP transport.

    The token list is read **live** from the data volume (managed in the app UI),
    so this container needs no secrets at deploy time and rotation is immediate.
    Non-HTTP scopes (lifespan, etc.) pass straight through.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])

        # No tokens configured yet → 503 so it's safe to deploy before setup.
        if not is_configured():
            await _send_json(
                send,
                503,
                b'{"error":"unconfigured","detail":"No MCP gateway token set. '
                b'Generate one in the app UI (Settings -> MCP)."}',
            )
            return

        # Presented bearer must match one of the active tokens (or the env override).
        raw = headers.get(b"authorization", b"").decode("latin-1")
        provided = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        if not verify(provided):
            await _send_json(
                send,
                401,
                b'{"error":"unauthorized","detail":"Missing or invalid bearer token."}',
                [(b"www-authenticate", b"Bearer")],
            )
            return

        await self.app(scope, receive, send)
