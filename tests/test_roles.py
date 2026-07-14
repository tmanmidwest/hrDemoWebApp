"""Tests for UI authorization roles (admin / management / view-only).

Roles govern the web UI only. We drive the HTML routes with TestClient and
assert that each role can reach exactly the surface it should, and that
forbidden routes redirect to /ui/employees (the shared safe landing) rather
than 500ing or leaking access.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

PASSWORD = "verysecure123"


def _create_local_user(username: str, role: str) -> None:
    """Insert a password-authenticating local user with the given role."""
    from app.db import get_session_factory
    from app.models import AppUser
    from app.services.passwords import hash_password

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        db.add(
            AppUser(
                username=username,
                password_hash=hash_password(PASSWORD),
                role=role,
                is_active=True,
                is_seeded=False,
            )
        )
        db.commit()


@pytest.fixture
def login_as(client: TestClient) -> Callable[[str], TestClient]:
    """Return a helper that creates a user with `role` and logs the client in."""

    def _login(role: str, username: str | None = None) -> TestClient:
        username = username or f"{role}_user"
        _create_local_user(username, role)
        resp = client.post(
            "/ui/login",
            data={"username": username, "password": PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        return client

    return _login


def _redirects_to_employees(resp) -> bool:
    return resp.status_code == 303 and resp.headers.get("location") == "/ui/employees"


# ---------------------------------------------------------------------------
# View Only
# ---------------------------------------------------------------------------


def test_view_only_can_see_employees_and_activity(login_as) -> None:
    c = login_as("view_only")
    assert c.get("/ui/employees").status_code == 200
    assert c.get("/ui/activity").status_code == 200


def test_view_only_cannot_open_employee_form(login_as) -> None:
    c = login_as("view_only")
    resp = c.get("/ui/employees/new", follow_redirects=False)
    assert _redirects_to_employees(resp)


def test_view_only_cannot_create_employee(login_as) -> None:
    c = login_as("view_only")
    resp = c.post("/ui/employees/new", data={}, follow_redirects=False)
    assert _redirects_to_employees(resp)


def test_view_only_cannot_see_lookups_or_settings(login_as) -> None:
    c = login_as("view_only")
    assert _redirects_to_employees(
        c.get("/ui/lookups/countries", follow_redirects=False)
    )
    assert _redirects_to_employees(c.get("/ui/settings", follow_redirects=False))


def test_view_only_sidebar_hides_manage_actions(login_as) -> None:
    c = login_as("view_only")
    body = c.get("/ui/employees").text
    assert "+ Add Employee" not in body
    # No Lookups or Settings/Admin nav for view-only.
    assert 'href="/ui/settings"' not in body
    assert 'href="/ui/lookups/countries"' not in body
    assert "View Only" in body  # role label in the sidebar footer


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------


def test_management_can_manage_employees(login_as) -> None:
    c = login_as("management")
    assert c.get("/ui/employees").status_code == 200
    assert c.get("/ui/employees/new").status_code == 200
    body = c.get("/ui/employees").text
    assert "+ Add Employee" in body


def test_management_can_view_but_not_manage_lookups(login_as) -> None:
    c = login_as("management")
    # List is viewable...
    resp = c.get("/ui/lookups/countries")
    assert resp.status_code == 200
    # ...but no add/edit affordance, and the mutation routes are blocked.
    assert "+ Add Country" not in resp.text
    assert _redirects_to_employees(
        c.get("/ui/lookups/countries/new", follow_redirects=False)
    )
    assert _redirects_to_employees(
        c.post(
            "/ui/lookups/countries/new",
            data={"code": "zz", "name": "Atlantis"},
            follow_redirects=False,
        )
    )


def test_management_cannot_reach_settings(login_as) -> None:
    c = login_as("management")
    assert _redirects_to_employees(c.get("/ui/settings", follow_redirects=False))
    assert _redirects_to_employees(
        c.get("/ui/settings/admin-users", follow_redirects=False)
    )


def test_management_sidebar_shows_lookups_not_settings(login_as) -> None:
    c = login_as("management")
    body = c.get("/ui/employees").text
    assert 'href="/ui/lookups/countries"' in body
    assert 'href="/ui/settings"' not in body


# ---------------------------------------------------------------------------
# Admin (seeded)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    from app.config import get_settings

    settings = get_settings()
    resp = client.post(
        "/ui/login",
        data={
            "username": settings.initial_admin_username,
            "password": settings.initial_admin_password,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


def test_admin_reaches_settings_and_lookup_mutations(admin_client: TestClient) -> None:
    assert admin_client.get("/ui/settings").status_code == 200
    assert admin_client.get("/ui/settings/admin-users").status_code == 200
    assert admin_client.get("/ui/lookups/countries/new").status_code == 200


def test_admin_sidebar_shows_settings_link(admin_client: TestClient) -> None:
    body = admin_client.get("/ui/employees").text
    assert 'href="/ui/settings"' in body
    assert "+ Add Employee" in body


# ---------------------------------------------------------------------------
# Role assignment: create + change
# ---------------------------------------------------------------------------


def test_create_user_persists_chosen_role(admin_client: TestClient) -> None:
    resp = admin_client.post(
        "/ui/settings/admin-users/new",
        data={"username": "mgr1", "password": PASSWORD, "role": "management"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    from app.db import get_session_factory
    from app.models import AppUser

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        u = db.query(AppUser).filter(AppUser.username == "mgr1").one()
        assert u.role == "management"


def test_create_user_unknown_role_falls_back_to_view_only(
    admin_client: TestClient,
) -> None:
    resp = admin_client.post(
        "/ui/settings/admin-users/new",
        data={"username": "bogus", "password": PASSWORD, "role": "superuser"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    from app.db import get_session_factory
    from app.models import AppUser

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        u = db.query(AppUser).filter(AppUser.username == "bogus").one()
        assert u.role == "view_only"


def test_change_role_of_another_user(admin_client: TestClient) -> None:
    _create_local_user("promoteme", "view_only")
    from app.db import get_session_factory
    from app.models import AppUser

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        target = db.query(AppUser).filter(AppUser.username == "promoteme").one()
        target_id = target.id

    resp = admin_client.post(
        f"/ui/settings/admin-users/{target_id}/role",
        data={"role": "management"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        assert db.get(AppUser, target_id).role == "management"


def test_cannot_change_seeded_admin_role(admin_client: TestClient) -> None:
    from app.db import get_session_factory
    from app.models import AppUser

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        seeded = db.query(AppUser).filter(AppUser.is_seeded.is_(True)).one()
        seeded_id = seeded.id

    resp = admin_client.post(
        f"/ui/settings/admin-users/{seeded_id}/role",
        data={"role": "view_only"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        assert db.get(AppUser, seeded_id).role == "admin"  # unchanged


# ---------------------------------------------------------------------------
# Enable / disable (UI)
# ---------------------------------------------------------------------------


def _user_by_name(username: str):
    from app.db import get_session_factory
    from app.models import AppUser

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        return db.query(AppUser).filter(AppUser.username == username).one()


def test_admin_can_disable_and_enable_user(admin_client: TestClient) -> None:
    _create_local_user("toggleme", "view_only")
    uid = _user_by_name("toggleme").id

    resp = admin_client.post(
        f"/ui/settings/admin-users/{uid}/disable", follow_redirects=False
    )
    assert resp.status_code == 303
    assert _user_by_name("toggleme").is_active is False

    resp = admin_client.post(
        f"/ui/settings/admin-users/{uid}/enable", follow_redirects=False
    )
    assert resp.status_code == 303
    assert _user_by_name("toggleme").is_active is True


def test_cannot_disable_seeded_admin_via_ui(admin_client: TestClient) -> None:
    seeded_id = _user_by_name("robbytheadmin").id
    admin_client.post(
        f"/ui/settings/admin-users/{seeded_id}/disable", follow_redirects=False
    )
    assert _user_by_name("robbytheadmin").is_active is True


def test_cannot_disable_self_via_ui(client: TestClient) -> None:
    # A second admin who is not the seeded account.
    _create_local_user("otheradmin", "admin")
    resp = client.post(
        "/ui/login",
        data={"username": "otheradmin", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    uid = _user_by_name("otheradmin").id
    client.post(f"/ui/settings/admin-users/{uid}/disable", follow_redirects=False)
    assert _user_by_name("otheradmin").is_active is True


# ---------------------------------------------------------------------------
# SSO provisioning default
# ---------------------------------------------------------------------------


def test_oidc_provisions_view_only_user(client: TestClient) -> None:
    from app.db import get_session_factory
    from app.models import AuthProvider
    from app.services.oidc import find_or_create_user

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        provider = AuthProvider(
            slug="test-idp",
            display_name="Test IdP",
            issuer_url="https://idp.example.com",
            client_id="client-abc",
            client_secret_encrypted="",
            scopes="openid email",
            is_enabled=True,
        )
        db.add(provider)
        db.commit()

        user = find_or_create_user(
            db, provider, {"sub": "subject-123", "email": "sso@example.com"}
        )
        assert user.role == "view_only"
