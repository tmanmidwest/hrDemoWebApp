"""Tests for the user-governance guide (service + UI page + downloads)."""

from __future__ import annotations

import json


def test_build_schema_covers_the_lifecycle(_isolated_data_dir):
    from app.config import get_settings
    from app.db import get_session_factory
    from app.services.governance_schema import build_schema, schema_to_csv
    from app.services.migrations import run_migrations
    from app.services.seed_data import seed_database

    run_migrations()
    db = get_session_factory()()
    seed_database(db, get_settings())

    schema = build_schema(db)

    # Every lifecycle action the IGA needs is described, with method + path.
    ops = {op["action"]: op for op in schema["operations"]}
    for action in (
        "Create account",
        "Update account",
        "Change role (entitlement)",
        "Disable account (deprovision)",
        "Enable account (reprovision)",
        "List roles (entitlement catalog)",
    ):
        assert action in ops, f"missing operation: {action}"
    assert ops["Create account"]["method"] == "POST"
    assert ops["Create account"]["path"] == "/api/v1/users"
    assert ops["Disable account (deprovision)"]["path"] == "/api/v1/users/{id}/disable"
    assert ops["List roles (entitlement catalog)"]["path"] == "/api/v1/roles"

    # Account attributes: password is write-only, id/role present.
    attrs = {a["name"]: a for a in schema["account_attributes"]}
    assert attrs["password"]["access"] == "write-only"
    assert attrs["role"]["access"] == "read-write"
    assert attrs["id"]["access"] == "read-only"

    # Entitlements mirror the fixed three-role catalog.
    role_ids = {r["id"] for r in schema["entitlements"]["roles"]}
    assert role_ids == {"admin", "management", "view_only"}

    # Scopes and governance notes are present.
    assert {s["scope"] for s in schema["auth"]["scopes"]} == {
        "users:read",
        "users:write",
        "roles:read",
    }
    assert any("never hard-deleted" in n for n in schema["notes"])

    # CSV renders with a header and one row per attribute.
    csv_text = schema_to_csv(schema)
    assert csv_text.splitlines()[0] == (
        "name,type,access,required_on_create,updatable,description"
    )
    assert "password" in csv_text


def test_ui_governance_page_and_downloads(admin_session):
    client = admin_session
    assert client.get("/ui/admin/governance").status_code == 200

    j = client.get("/ui/admin/governance/export.json")
    assert j.status_code == 200
    assert j.headers["content-type"].startswith("application/json")
    parsed = json.loads(j.content)
    assert "operations" in parsed and "account_attributes" in parsed

    c = client.get("/ui/admin/governance/export.csv")
    assert c.status_code == 200
    assert "text/csv" in c.headers["content-type"]
    assert c.text.splitlines()[0].startswith("name,type,access")


def test_governance_page_is_admin_only(client):
    """Non-admins are redirected; anonymous users are sent to login."""
    from app.db import get_session_factory
    from app.models import AppUser
    from app.services.passwords import hash_password

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        db.add(
            AppUser(
                username="viewer",
                password_hash=hash_password("verysecure123"),
                role="view_only",
                is_active=True,
                is_seeded=False,
            )
        )
        db.commit()

    # Anonymous -> redirected to login.
    anon = client.get("/ui/admin/governance", follow_redirects=False)
    assert anon.status_code in (302, 303)
    assert "/ui/login" in anon.headers["location"]

    # View-only -> forbidden, redirected to the shared safe landing.
    client.post(
        "/ui/login",
        data={"username": "viewer", "password": "verysecure123"},
        follow_redirects=False,
    )
    resp = client.get("/ui/admin/governance", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/employees"
