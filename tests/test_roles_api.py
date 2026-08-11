"""Tests for the access-role catalog and role-assignment REST API.

This is the surface an IGA platform uses to read roles per user and to
provision / deprovision by granting / revoking assignments. Authenticated with
a bearer API key via the `api_client` / `auth_headers` conftest fixtures.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

ROLES = "/api/v1/roles/"
USERS = "/api/v1/users/"
ASSIGNMENTS = "/api/v1/roles/assignments"


def _make_user(api_client: TestClient, username: str) -> int:
    resp = api_client.post(
        USERS, json={"username": username, "password": "verysecure123"}
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


def _role_id(api_client: TestClient, name: str) -> int:
    roles = api_client.get(ROLES).json()
    return next(r["id"] for r in roles if r["name"] == name)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_roles_api_requires_auth(client: TestClient) -> None:
    assert client.get(ROLES).status_code == 401
    assert client.get(ASSIGNMENTS).status_code == 401


# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------


def test_list_roles_returns_seeded_catalog(api_client: TestClient) -> None:
    resp = api_client.get(ROLES)
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert {"HR Administrator", "Employee Self-Service"} <= names


def test_create_get_update_delete_role(api_client: TestClient) -> None:
    created = api_client.post(
        ROLES, json={"name": "Custom Role", "description": "desc"}
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert created.json()["is_active"] is True

    got = api_client.get(f"{ROLES}{rid}")
    assert got.status_code == 200
    assert got.json()["name"] == "Custom Role"

    patched = api_client.patch(
        f"{ROLES}{rid}", json={"description": "new", "is_active": False}
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "new"
    assert patched.json()["is_active"] is False

    deleted = api_client.delete(f"{ROLES}{rid}")
    assert deleted.status_code == 204
    assert api_client.get(f"{ROLES}{rid}").status_code == 404


def test_api_does_not_expose_access_level(api_client: TestClient) -> None:
    """`access_level` is a UI-only annotation; it must never reach the API/IGA."""
    roles = api_client.get(ROLES).json()
    assert roles, "expected seeded roles"
    for role in roles:
        assert "access_level" not in role
    one = api_client.get(f"{ROLES}{roles[0]['id']}").json()
    assert "access_level" not in one


def test_create_duplicate_role_conflict(api_client: TestClient) -> None:
    assert api_client.post(ROLES, json={"name": "Dup"}).status_code == 201
    assert api_client.post(ROLES, json={"name": "Dup"}).status_code == 409


def test_list_roles_is_active_filter(api_client: TestClient) -> None:
    rid = api_client.post(ROLES, json={"name": "Inactive One"}).json()["id"]
    api_client.patch(f"{ROLES}{rid}", json={"is_active": False})
    active = api_client.get(ROLES, params={"is_active": True}).json()
    assert all(r["is_active"] for r in active)
    assert "Inactive One" not in {r["name"] for r in active}


def test_delete_role_blocked_while_assigned(api_client: TestClient) -> None:
    uid = _make_user(api_client, "role_holder")
    rid = api_client.post(ROLES, json={"name": "Held Role"}).json()["id"]
    assert (
        api_client.post(f"{USERS}{uid}/roles", json={"role_id": rid}).status_code
        == 201
    )
    resp = api_client.delete(f"{ROLES}{rid}")
    assert resp.status_code == 409
    # Still present after the blocked delete.
    assert api_client.get(f"{ROLES}{rid}").status_code == 200


# ---------------------------------------------------------------------------
# Assignments — provision / deprovision
# ---------------------------------------------------------------------------


def test_grant_and_list_user_roles(api_client: TestClient) -> None:
    uid = _make_user(api_client, "prov_user")
    rid = _role_id(api_client, "HR Administrator")

    grant = api_client.post(f"{USERS}{uid}/roles", json={"role_id": rid})
    assert grant.status_code == 201, grant.text
    body = grant.json()
    assert body["role"]["name"] == "HR Administrator"
    assert body["user"]["id"] == uid
    assert "granted_at" in body

    listed = api_client.get(f"{USERS}{uid}/roles")
    assert listed.status_code == 200
    assert [r["role"]["id"] for r in listed.json()] == [rid]


def test_grant_duplicate_conflict(api_client: TestClient) -> None:
    uid = _make_user(api_client, "dup_grant")
    rid = _role_id(api_client, "HR Analyst")
    assert api_client.post(f"{USERS}{uid}/roles", json={"role_id": rid}).status_code == 201
    assert api_client.post(f"{USERS}{uid}/roles", json={"role_id": rid}).status_code == 409


def test_grant_inactive_role_rejected(api_client: TestClient) -> None:
    uid = _make_user(api_client, "inactive_grant")
    rid = api_client.post(ROLES, json={"name": "Disabled Role"}).json()["id"]
    api_client.patch(f"{ROLES}{rid}", json={"is_active": False})
    resp = api_client.post(f"{USERS}{uid}/roles", json={"role_id": rid})
    assert resp.status_code == 400


def test_grant_unknown_role_404(api_client: TestClient) -> None:
    uid = _make_user(api_client, "no_role")
    assert api_client.post(f"{USERS}{uid}/roles", json={"role_id": 999999}).status_code == 404


def test_grant_to_unknown_user_404(api_client: TestClient) -> None:
    rid = _role_id(api_client, "Recruiter")
    assert api_client.post(f"{USERS}999999/roles", json={"role_id": rid}).status_code == 404


def test_revoke_role(api_client: TestClient) -> None:
    uid = _make_user(api_client, "revoke_user")
    rid = _role_id(api_client, "IT Auditor")
    api_client.post(f"{USERS}{uid}/roles", json={"role_id": rid})

    revoke = api_client.delete(f"{USERS}{uid}/roles/{rid}")
    assert revoke.status_code == 204
    assert api_client.get(f"{USERS}{uid}/roles").json() == []
    # Revoking again is a 404 (not held).
    assert api_client.delete(f"{USERS}{uid}/roles/{rid}").status_code == 404


# ---------------------------------------------------------------------------
# Reconciliation feed
# ---------------------------------------------------------------------------


def test_assignments_feed_and_filters(api_client: TestClient) -> None:
    u1 = _make_user(api_client, "feed_a")
    u2 = _make_user(api_client, "feed_b")
    r1 = _role_id(api_client, "HR Administrator")
    r2 = _role_id(api_client, "Payroll Processor")
    api_client.post(f"{USERS}{u1}/roles", json={"role_id": r1})
    api_client.post(f"{USERS}{u2}/roles", json={"role_id": r2})

    everything = api_client.get(ASSIGNMENTS)
    assert everything.status_code == 200
    pairs = {(a["user"]["id"], a["role"]["id"]) for a in everything.json()}
    assert {(u1, r1), (u2, r2)} <= pairs

    by_user = api_client.get(ASSIGNMENTS, params={"user_id": u1}).json()
    assert {a["user"]["id"] for a in by_user} == {u1}

    by_role = api_client.get(ASSIGNMENTS, params={"role_id": r2}).json()
    assert {a["role"]["id"] for a in by_role} == {r2}


def test_assignments_updated_since_incremental(api_client: TestClient) -> None:
    u1 = _make_user(api_client, "since_a")
    r1 = _role_id(api_client, "Manager Self-Service")
    first = api_client.post(f"{USERS}{u1}/roles", json={"role_id": r1}).json()
    cutoff = first["updated_at"]

    # Nothing granted at-or-after its own timestamp except itself; a strictly
    # later grant should show up when we filter from just after the cutoff.
    u2 = _make_user(api_client, "since_b")
    r2 = _role_id(api_client, "Recruiter")
    api_client.post(f"{USERS}{u2}/roles", json={"role_id": r2})

    recent = api_client.get(ASSIGNMENTS, params={"updated_since": cutoff}).json()
    ids = {(a["user"]["id"], a["role"]["id"]) for a in recent}
    assert (u2, r2) in ids
