"""Manage the inbound MCP gateway tokens (bearer secrets external apps present).

These are multiple named, individually revocable tokens — one per consuming
project/app — managed from the UI like API keys. The records live in the
``mcp_gateway_tokens`` table.

The catch: the MCP server runs as a **separate container with no database access**
(it only shares the data volume and reaches the app over HTTP). So it can't query
these rows. Instead the app writes the *hashes* of the currently-active tokens to
a JSON file on the shared volume (``<data_dir>/mcp_gateway_tokens.json``), and the
MCP server verifies presented tokens against that file, read live on each request.
Creating or revoking a token re-syncs the file, so changes take effect on the MCP
server's very next call with no restart.

The full token is returned only once, at creation; only its SHA-256 hash and a
short prefix are persisted.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import McpGatewayToken
from app.services.tokens import hash_token

log = logging.getLogger(__name__)

ACTIVE_TOKENS_FILENAME = "mcp_gateway_tokens.json"
TOKEN_PREFIX = "hrsotgw_"
TOKEN_RANDOM_LEN = 32


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_token() -> tuple[str, str]:
    """Generate a new gateway token. Returns (full_token, prefix_for_display).

    Format: ``hrsotgw_<32 url-safe chars>``. The prefix is the first 14 characters
    (incl. ``hrsotgw_``), enough to identify "which token" without revealing it.
    """
    random_part = secrets.token_urlsafe(TOKEN_RANDOM_LEN)[:TOKEN_RANDOM_LEN]
    full = f"{TOKEN_PREFIX}{random_part}"
    return full, full[:14]


# ---------------------------------------------------------------------------
# Synced active-token file (read by the DB-less MCP server)
# ---------------------------------------------------------------------------


def active_tokens_path(settings: Settings | None = None) -> Path:
    """Path to the file holding active token hashes on the data volume."""
    return (settings or get_settings()).data_dir / ACTIVE_TOKENS_FILENAME


def sync_active_tokens(db: Session, settings: Settings | None = None) -> int:
    """Rewrite the active-token file from the DB. Returns the active count."""
    settings = settings or get_settings()
    rows = (
        db.query(McpGatewayToken)
        .filter(McpGatewayToken.revoked_at.is_(None))
        .order_by(McpGatewayToken.created_at.asc())
        .all()
    )
    payload = [
        {"name": r.name, "prefix": r.token_prefix, "hash": r.token_hash} for r in rows
    ]

    settings.ensure_data_dir()
    path = active_tokens_path(settings)
    path.write_text(json.dumps(payload))
    try:
        path.chmod(0o600)
    except OSError:
        pass  # some mounted volumes don't support chmod

    return len(payload)


# ---------------------------------------------------------------------------
# CRUD (called from the app UI; these touch the DB and re-sync the file)
# ---------------------------------------------------------------------------


def list_tokens(db: Session) -> list[McpGatewayToken]:
    """All gateway tokens, newest first, for the settings page."""
    return db.query(McpGatewayToken).order_by(McpGatewayToken.created_at.desc()).all()


def create(
    db: Session, *, name: str, actor_id: int, settings: Settings | None = None
) -> tuple[McpGatewayToken, str]:
    """Mint a named token, persist its hash, sync the file, return (row, plaintext).

    The plaintext is only available here — afterward only the prefix is shown.
    """
    full, prefix = generate_token()
    row = McpGatewayToken(
        name=name.strip(),
        token_prefix=prefix,
        token_hash=hash_token(full),
        created_by_user_id=actor_id,
    )
    db.add(row)
    db.commit()
    sync_active_tokens(db, settings)
    log.info(
        "mcp_gateway_token_created",
        extra={"token_id": row.id, "token_name": row.name, "prefix": prefix, "by": actor_id},
    )
    return row, full


def revoke(
    db: Session, token_id: int, settings: Settings | None = None
) -> McpGatewayToken | None:
    """Revoke a token so it stops authenticating. Returns the row, or None."""
    row = db.get(McpGatewayToken, token_id)
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
        sync_active_tokens(db, settings)
        log.info("mcp_gateway_token_revoked", extra={"token_id": token_id})
    return row


def delete(db: Session, token_id: int, settings: Settings | None = None) -> str | None:
    """Delete a token record entirely. Returns its name, or None if not found."""
    row = db.get(McpGatewayToken, token_id)
    if row is None:
        return None
    name = row.name
    db.delete(row)
    db.commit()
    sync_active_tokens(db, settings)
    log.info(
        "mcp_gateway_token_deleted", extra={"token_id": token_id, "token_name": name}
    )
    return name
