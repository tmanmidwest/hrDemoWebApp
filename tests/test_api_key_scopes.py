"""Enforcement tests for scoped API keys.

Keys are minted with specific scopes via the session-authed create endpoint,
then used as bearer tokens to prove each scope gate allows/denies correctly.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _make_key(admin_session: TestClient, scopes: list[str] | None) -> dict[str, str]:
    body: dict = {"name": "scope-test"}
    if scopes is not None:
        body["scopes"] = scopes
    resp = admin_session.post("/api/v1/auth/api-keys/", json=body)
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['key']}"}


# ---------------------------------------------------------------------------
# Read-only / employee-management presets
# ---------------------------------------------------------------------------


def test_employees_read_only_key(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["employees:read"])
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 200
    # Write is denied by scope (403), before any body validation.
    assert admin_session.post("/api/v1/employees/", headers=h, json={}).status_code == 403
    # Other resources are off-limits too.
    assert admin_session.get("/api/v1/users/", headers=h).status_code == 403
    assert admin_session.post("/api/v1/backup", headers=h).status_code == 403
    assert admin_session.get("/api/v1/countries/", headers=h).status_code == 403


def test_employee_management_preset(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["employees:read", "employees:write", "lookups:read"])
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 200
    assert admin_session.get("/api/v1/countries/", headers=h).status_code == 200
    # employees:write passes the scope gate (fails later on body validation, not 403).
    assert admin_session.post("/api/v1/employees/", headers=h, json={}).status_code != 403
    # No users / backup / lookup-write access.
    assert admin_session.get("/api/v1/users/", headers=h).status_code == 403
    assert admin_session.post("/api/v1/backup", headers=h).status_code == 403
    assert admin_session.post(
        "/api/v1/countries/", headers=h, json={"code": "ZZ", "name": "Atlantis"}
    ).status_code == 403


def test_read_only_view_all_preset(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["employees:read", "lookups:read", "users:read"])
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 200
    assert admin_session.get("/api/v1/countries/", headers=h).status_code == 200
    assert admin_session.get("/api/v1/users/", headers=h).status_code == 200
    # Every write is denied.
    assert admin_session.post("/api/v1/employees/", headers=h, json={}).status_code == 403
    assert admin_session.post(
        "/api/v1/users/", headers=h, json={"username": "x", "password": "verysecure123"}
    ).status_code == 403
    assert admin_session.post("/api/v1/backup", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# Individual write scopes actually permit their writes
# ---------------------------------------------------------------------------


def test_users_write_scope_allows_create(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["users:read", "users:write"])
    resp = admin_session.post(
        "/api/v1/users/",
        headers=h,
        json={"username": "scoped_user", "password": "verysecure123", "role": "view_only"},
    )
    assert resp.status_code == 201, resp.text


def test_lookups_write_scope_allows_create(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["lookups:read", "lookups:write"])
    resp = admin_session.post(
        "/api/v1/countries/", headers=h, json={"code": "ZZ", "name": "Atlantis"}
    )
    assert resp.status_code == 201, resp.text


def test_backup_scope_allows_backup_only(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["backup:create"])
    assert admin_session.post("/api/v1/backup", headers=h).status_code == 200
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# Admin + backward compatibility
# ---------------------------------------------------------------------------


def test_admin_scope_allows_everything(admin_session: TestClient) -> None:
    h = _make_key(admin_session, ["admin"])
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 200
    assert admin_session.post(
        "/api/v1/users/",
        headers=h,
        json={"username": "admin_made", "password": "verysecure123"},
    ).status_code == 201
    assert admin_session.post("/api/v1/backup", headers=h).status_code == 200


def test_omitted_scopes_default_to_admin(admin_session: TestClient) -> None:
    # Backward compat: existing automation that creates keys without scopes.
    resp = admin_session.post("/api/v1/auth/api-keys/", json={"name": "legacy"})
    assert resp.status_code == 201
    assert resp.json()["scopes"] == ["admin"]
    h = {"Authorization": f"Bearer {resp.json()['key']}"}
    assert admin_session.get("/api/v1/employees/", headers=h).status_code == 200
    assert admin_session.post("/api/v1/backup", headers=h).status_code == 200


def test_create_key_with_only_unknown_scopes_rejected(admin_session: TestClient) -> None:
    resp = admin_session.post(
        "/api/v1/auth/api-keys/", json={"name": "bad", "scopes": ["bogus:scope"]}
    )
    assert resp.status_code == 422


def test_scoped_endpoints_still_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/employees/").status_code == 401
