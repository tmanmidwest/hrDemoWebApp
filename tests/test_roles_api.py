"""Tests for the read-only role (entitlement) catalog REST API.

Roles are the fixed console-account authorization levels (view_only /
management / admin), backed by the `UserRole` enum. An IGA platform imports
this catalog as its entitlement list and correlates on `id`; a user's assigned
role is read/written through the users API, not a separate assignment resource.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

ROLES = "/api/v1/roles/"
USERS = "/api/v1/users/"

EXPECTED_IDS = {"admin", "management", "view_only"}


def _make_key(admin_session: TestClient, scopes: list[str] | None) -> dict[str, str]:
    body: dict = {"name": "roles-scope-test"}
    if scopes is not None:
        body["scopes"] = scopes
    resp = admin_session.post("/api/v1/auth/api-keys/", json=body)
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['key']}"}


# ---------------------------------------------------------------------------
# Auth / scope
# ---------------------------------------------------------------------------


def test_roles_api_requires_auth(client: TestClient) -> None:
    assert client.get(ROLES).status_code == 401


def test_roles_read_scope_gates_listing(admin_session: TestClient) -> None:
    with_read = _make_key(admin_session, ["roles:read"])
    assert admin_session.get(ROLES, headers=with_read).status_code == 200
    # A key without roles:read is denied (scope gate → 403).
    without = _make_key(admin_session, ["employees:read"])
    assert admin_session.get(ROLES, headers=without).status_code == 403


# ---------------------------------------------------------------------------
# Catalog contents
# ---------------------------------------------------------------------------


def test_list_returns_the_three_fixed_roles(api_client: TestClient) -> None:
    resp = api_client.get(ROLES)
    assert resp.status_code == 200
    roles = resp.json()
    assert {r["id"] for r in roles} == EXPECTED_IDS
    # Ordered most- to least-privileged.
    assert [r["id"] for r in roles] == ["admin", "management", "view_only"]
    for role in roles:
        assert set(role) == {"id", "name", "description"}
        assert role["name"] and role["description"]


def test_catalog_present_regardless_of_assignment(api_client: TestClient) -> None:
    """The entitlement catalog exists even when no user holds a given role.

    Only the seeded admin exists here, yet management and view_only still list —
    Saviynt can import every entitlement before anyone is assigned to it.
    """
    users = api_client.get(USERS).json()
    assert {u["role"] for u in users} == {"admin"}  # just the seeded admin
    ids = {r["id"] for r in api_client.get(ROLES).json()}
    assert {"management", "view_only"} <= ids


def test_role_ids_match_user_role_values(api_client: TestClient) -> None:
    """A catalog `id` is exactly the value that appears as a user's `role`."""
    uid = api_client.post(
        USERS, json={"username": "mgr", "password": "verysecure123", "role": "management"}
    ).json()["id"]
    user = api_client.get(f"{USERS}{uid}").json()
    catalog_ids = {r["id"] for r in api_client.get(ROLES).json()}
    assert user["role"] == "management"
    assert user["role"] in catalog_ids


def test_catalog_never_exposes_access_level(api_client: TestClient) -> None:
    """The former UI-only annotation must not leak through the API."""
    for role in api_client.get(ROLES).json():
        assert "access_level" not in role


# ---------------------------------------------------------------------------
# Read-only: the old mutation / assignment surface is gone
# ---------------------------------------------------------------------------


def test_catalog_is_read_only(api_client: TestClient) -> None:
    # Only GET is defined on the collection — writes are 405 Method Not Allowed.
    assert api_client.post(ROLES, json={"name": "Custom"}).status_code == 405
    # Former per-role paths no longer exist.
    assert api_client.get("/api/v1/roles/admin").status_code == 404
    assert api_client.patch("/api/v1/roles/admin", json={}).status_code == 404
    assert api_client.delete("/api/v1/roles/admin").status_code == 404


def test_assignment_endpoints_removed(api_client: TestClient) -> None:
    """The per-user grant/revoke and reconciliation feed are gone."""
    uid = api_client.post(
        USERS, json={"username": "noroles", "password": "verysecure123"}
    ).json()["id"]
    assert api_client.get("/api/v1/roles/assignments").status_code == 404
    assert api_client.get(f"{USERS}{uid}/roles").status_code == 404
    assert api_client.post(f"{USERS}{uid}/roles", json={"role_id": 1}).status_code == 404
    assert api_client.delete(f"{USERS}{uid}/roles/1").status_code == 404


# ---------------------------------------------------------------------------
# Assignment now happens on the user record (the single-valued `role` attr)
# ---------------------------------------------------------------------------


def test_role_assigned_and_changed_via_users_api(api_client: TestClient) -> None:
    uid = api_client.post(
        USERS, json={"username": "prov", "password": "verysecure123", "role": "view_only"}
    ).json()["id"]
    assert api_client.get(f"{USERS}{uid}").json()["role"] == "view_only"

    patched = api_client.patch(f"{USERS}{uid}", json={"role": "admin"})
    assert patched.status_code == 200
    assert patched.json()["role"] == "admin"
