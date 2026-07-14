"""Tests for the console-user (AppUser) management REST API.

Authenticated with a bearer API key via the `api_client` / `auth_headers`
conftest fixtures.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

USERS = "/api/v1/users/"


def _seeded_user(api_client: TestClient) -> dict:
    users = api_client.get(USERS).json()
    return next(u for u in users if u["is_seeded"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_users_api_requires_auth(client: TestClient) -> None:
    assert client.get(USERS).status_code == 401


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


def test_list_users_includes_seeded_admin(api_client: TestClient) -> None:
    resp = api_client.get(USERS)
    assert resp.status_code == 200
    users = resp.json()
    seeded = [u for u in users if u["is_seeded"]]
    assert len(seeded) == 1
    assert seeded[0]["role"] == "admin"
    assert seeded[0]["auth_type"] == "local"
    # The password hash must never be exposed.
    assert "password_hash" not in users[0]


def test_get_user_404(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/users/999999").status_code == 404


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_user_persists_role(api_client: TestClient) -> None:
    resp = api_client.post(
        USERS,
        json={"username": "api_mgr", "password": "verysecure123", "role": "management"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "api_mgr"
    assert body["role"] == "management"
    assert body["is_active"] is True
    assert body["auth_type"] == "local"


def test_create_user_defaults_to_view_only(api_client: TestClient) -> None:
    resp = api_client.post(
        USERS, json={"username": "api_default", "password": "verysecure123"}
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "view_only"


def test_create_user_duplicate_conflict(api_client: TestClient) -> None:
    resp = api_client.post(
        USERS, json={"username": "robbytheadmin", "password": "verysecure123"}
    )
    assert resp.status_code == 409


def test_create_user_bad_role_422(api_client: TestClient) -> None:
    resp = api_client.post(
        USERS, json={"username": "api_bad", "password": "verysecure123", "role": "superuser"}
    )
    assert resp.status_code == 422


def test_create_user_short_password_422(api_client: TestClient) -> None:
    resp = api_client.post(USERS, json={"username": "api_weak", "password": "short"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_user_role_and_password(api_client: TestClient) -> None:
    created = api_client.post(
        USERS, json={"username": "api_up", "password": "verysecure123"}
    ).json()
    resp = api_client.patch(
        f"/api/v1/users/{created['id']}",
        json={"role": "management", "password": "newsecret123"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "management"


def test_cannot_change_seeded_admin_role(api_client: TestClient) -> None:
    seeded = _seeded_user(api_client)
    resp = api_client.patch(f"/api/v1/users/{seeded['id']}", json={"role": "view_only"})
    assert resp.status_code == 400
    # unchanged
    assert _seeded_user(api_client)["role"] == "admin"


def test_update_username_conflict_409(api_client: TestClient) -> None:
    a = api_client.post(
        USERS, json={"username": "api_a", "password": "verysecure123"}
    ).json()
    api_client.post(USERS, json={"username": "api_b", "password": "verysecure123"})
    resp = api_client.patch(f"/api/v1/users/{a['id']}", json={"username": "api_b"})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Disable / enable
# ---------------------------------------------------------------------------


def test_disable_then_enable_user(api_client: TestClient) -> None:
    created = api_client.post(
        USERS, json={"username": "api_toggle", "password": "verysecure123"}
    ).json()
    uid = created["id"]

    disabled = api_client.post(f"/api/v1/users/{uid}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    enabled = api_client.post(f"/api/v1/users/{uid}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["is_active"] is True


def test_cannot_disable_seeded_admin(api_client: TestClient) -> None:
    seeded = _seeded_user(api_client)
    resp = api_client.post(f"/api/v1/users/{seeded['id']}/disable")
    assert resp.status_code == 400


def test_disabled_user_cannot_log_in(api_client: TestClient) -> None:
    api_client.post(
        USERS, json={"username": "api_locked", "password": "verysecure123"}
    )
    # Confirm the account works before disabling.
    ok = api_client.post(
        "/api/v1/auth/session/login",
        json={"username": "api_locked", "password": "verysecure123"},
    )
    assert ok.status_code == 200

    users = api_client.get(USERS).json()
    uid = next(u["id"] for u in users if u["username"] == "api_locked")
    api_client.post(f"/api/v1/users/{uid}/disable")

    denied = api_client.post(
        "/api/v1/auth/session/login",
        json={"username": "api_locked", "password": "verysecure123"},
    )
    assert denied.status_code == 401


# ---------------------------------------------------------------------------
# No delete over the API
# ---------------------------------------------------------------------------


def test_delete_user_not_allowed(api_client: TestClient) -> None:
    created = api_client.post(
        USERS, json={"username": "api_nodelete", "password": "verysecure123"}
    ).json()
    resp = api_client.delete(f"/api/v1/users/{created['id']}")
    assert resp.status_code == 405
