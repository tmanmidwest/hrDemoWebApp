"""Tests for MCP token management: the outbound service key and the inbound
gateway tokens, the Settings → MCP UI routes, and the contract with the MCP
server's own gateway-auth verification.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings


def _data_dir() -> Path:
    return get_settings().data_dir


def _mcp_api_key_file() -> Path:
    return _data_dir() / "mcp_api_key"


def _gateway_file() -> Path:
    return _data_dir() / "mcp_gateway_tokens.json"


# ---------------------------------------------------------------------------
# Outbound service token (MCP server → app)
# ---------------------------------------------------------------------------


def test_rotate_creates_scoped_key_and_file(admin_session: TestClient) -> None:
    r = admin_session.post("/ui/settings/mcp/rotate", follow_redirects=False)
    assert r.status_code == 303

    token = _mcp_api_key_file().read_text().strip()
    assert token.startswith("hrsot_")

    # Revealed exactly once.
    assert token in admin_session.get("/ui/settings/mcp").text
    assert token not in admin_session.get("/ui/settings/mcp").text

    # Least privilege: reads + reports work; user management does not.
    h = {"Authorization": f"Bearer {token}"}
    assert admin_session.get("/api/v1/reports/headcount", headers=h).status_code == 200
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 200
    assert admin_session.get("/api/v1/users/", headers=h).status_code == 403


def test_rotate_revokes_previous(admin_session: TestClient) -> None:
    admin_session.post("/ui/settings/mcp/rotate")
    old = _mcp_api_key_file().read_text().strip()
    admin_session.post("/ui/settings/mcp/rotate")
    new = _mcp_api_key_file().read_text().strip()

    assert old != new
    assert admin_session.get(
        "/api/v1/employees/", headers={"Authorization": f"Bearer {old}"}
    ).status_code == 401
    assert admin_session.get(
        "/api/v1/employees/", headers={"Authorization": f"Bearer {new}"}
    ).status_code == 200


def test_clear_revokes_and_removes_file(admin_session: TestClient) -> None:
    admin_session.post("/ui/settings/mcp/rotate")
    token = _mcp_api_key_file().read_text().strip()
    admin_session.post("/ui/settings/mcp/clear")

    assert not _mcp_api_key_file().exists()
    assert admin_session.get(
        "/api/v1/employees/", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


def test_mcp_page_renders(admin_session: TestClient) -> None:
    r = admin_session.get("/ui/settings/mcp")
    assert r.status_code == 200
    assert "MCP Server" in r.text
    assert "Connect a Claude client" in r.text


def test_missing_outbound_token_notice(admin_session: TestClient) -> None:
    """The inbound section warns when the outbound API token isn't set yet, and
    the warning clears once it is generated."""
    notice = "No MCP server API token yet."
    assert notice in admin_session.get("/ui/settings/mcp").text
    admin_session.post("/ui/settings/mcp/rotate")
    assert notice not in admin_session.get("/ui/settings/mcp").text


# ---------------------------------------------------------------------------
# Inbound gateway tokens (client → MCP server)
# ---------------------------------------------------------------------------


def test_gateway_token_create_syncs_file(admin_session: TestClient) -> None:
    r = admin_session.post(
        "/ui/settings/mcp/gateway/tokens/new",
        data={"name": "Saviynt prod"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Revealed once.
    assert "hrsotgw_" in admin_session.get("/ui/settings/mcp").text

    data = json.loads(_gateway_file().read_text())
    assert len(data) == 1
    assert data[0]["name"] == "Saviynt prod"
    assert data[0]["prefix"].startswith("hrsotgw_")
    assert len(data[0]["hash"]) == 64  # SHA-256 hex


def test_gateway_token_revoke_and_delete(admin_session: TestClient) -> None:
    admin_session.post("/ui/settings/mcp/gateway/tokens/new", data={"name": "A"})
    admin_session.post("/ui/settings/mcp/gateway/tokens/new", data={"name": "B"})
    assert len(json.loads(_gateway_file().read_text())) == 2

    admin_session.post("/ui/settings/mcp/gateway/tokens/1/revoke")
    assert len(json.loads(_gateway_file().read_text())) == 1

    admin_session.post("/ui/settings/mcp/gateway/tokens/2/delete")
    assert json.loads(_gateway_file().read_text()) == []


def test_gateway_token_requires_name(admin_session: TestClient) -> None:
    r = admin_session.post(
        "/ui/settings/mcp/gateway/tokens/new",
        data={"name": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Nothing created → the sync file was never written (or is empty).
    assert not _gateway_file().exists() or json.loads(_gateway_file().read_text()) == []


# ---------------------------------------------------------------------------
# The MCP key is protected on the regular API-keys page
# ---------------------------------------------------------------------------


def test_mcp_key_protected_on_api_keys_page(admin_session: TestClient) -> None:
    admin_session.post("/ui/settings/mcp/rotate")
    token = _mcp_api_key_file().read_text().strip()

    page = admin_session.get("/ui/settings/api-keys")
    assert "Manage on MCP page" in page.text

    from app.db import get_session_factory
    from app.services import mcp_token

    with get_session_factory()() as db:
        mcp_id = mcp_token.current_key_id(db)

    # Revoke and delete via the api-keys routes are both blocked and redirect.
    for action in ("revoke", "delete"):
        r = admin_session.post(
            f"/ui/settings/api-keys/{mcp_id}/{action}", follow_redirects=False
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/ui/settings/mcp"
        # Key still authenticates.
        assert admin_session.get(
            "/api/v1/employees/", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200


# ---------------------------------------------------------------------------
# Contract: the MCP server's gateway-auth verifies what the app syncs
# ---------------------------------------------------------------------------


def test_mcp_gateway_auth_verify_contract(
    admin_session: TestClient, monkeypatch
) -> None:
    """The standalone MCP server validates inbound tokens against the JSON the
    app writes — exercise that boundary directly."""
    from app.db import get_session_factory
    from app.services import mcp_gateway_tokens

    data_dir = _data_dir()
    with get_session_factory()() as db:
        _row, full = mcp_gateway_tokens.create(db, name="contract", actor_id=1)

    # Point the MCP server's own config at the same data volume.
    monkeypatch.setenv("HRMCP_DATA_DIR", str(data_dir))
    import mcp_server.config as mcfg

    mcfg.get_settings.cache_clear()
    import mcp_server.gateway_auth as ga

    assert ga.is_configured() is True
    assert ga.verify(full) is True
    assert ga.verify("hrsotgw_wrongwrongwrong") is False
    assert ga.verify("") is False

    # Revoking in the app removes it from the synced file → verify now fails.
    with get_session_factory()() as db:
        row = mcp_gateway_tokens.list_tokens(db)[0]
        mcp_gateway_tokens.revoke(db, row.id)
    assert ga.verify(full) is False
    assert ga.is_configured() is False
    mcfg.get_settings.cache_clear()
