"""Tests for self-service change-password (/ui/account/password).

Available to every signed-in user regardless of role. Changing the password
requires the current one, and the plaintext is never written to the audit log.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

PASSWORD = "verysecure123"
NEW_PASSWORD = "brandnewpw456"


def _create_local_user(username: str, role: str, password: str = PASSWORD) -> int:
    from app.db import get_session_factory
    from app.models import AppUser
    from app.services.passwords import hash_password

    with get_session_factory()() as db:
        u = AppUser(
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            is_seeded=False,
        )
        db.add(u)
        db.commit()
        return u.id


@pytest.fixture
def login_as(client: TestClient) -> Callable[..., TestClient]:
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


def _password_hash(username: str) -> str | None:
    from app.db import get_session_factory
    from app.models import AppUser

    with get_session_factory()() as db:
        return db.query(AppUser).filter(AppUser.username == username).one().password_hash


# ---------------------------------------------------------------------------
# Access + happy path (every role)
# ---------------------------------------------------------------------------


def test_change_password_requires_login(client: TestClient) -> None:
    resp = client.get("/ui/account/password", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/login" in resp.headers["location"]


@pytest.mark.parametrize("role", ["view_only", "management", "admin"])
def test_any_role_can_change_own_password(login_as, role: str) -> None:
    c = login_as(role)
    assert c.get("/ui/account/password").status_code == 200

    before = _password_hash(f"{role}_user")
    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/employees"

    after = _password_hash(f"{role}_user")
    assert after != before  # hash actually changed

    # The new password works for a fresh login; the old one no longer does.
    c.post("/ui/logout", follow_redirects=False)
    assert c.post(
        "/ui/login",
        data={"username": f"{role}_user", "password": NEW_PASSWORD},
        follow_redirects=False,
    ).status_code == 303
    assert c.post(
        "/ui/login",
        data={"username": f"{role}_user", "password": PASSWORD},
        follow_redirects=False,
    ).status_code != 303


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_wrong_current_password_rejected(login_as) -> None:
    c = login_as("management")
    before = _password_hash("management_user")
    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": "not-the-password",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert resp.status_code == 200
    assert "current password is incorrect" in resp.text.lower()
    assert _password_hash("management_user") == before  # unchanged


def test_mismatched_new_passwords_rejected(login_as) -> None:
    c = login_as("view_only")
    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": "different-value",
        },
    )
    assert resp.status_code == 200
    assert "do not match" in resp.text.lower()


def test_short_new_password_rejected(login_as) -> None:
    c = login_as("view_only")
    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert resp.status_code == 200
    assert "at least 8" in resp.text.lower()


def test_same_password_rejected(login_as) -> None:
    c = login_as("admin")
    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )
    assert resp.status_code == 200
    assert "different" in resp.text.lower()


# ---------------------------------------------------------------------------
# SSO accounts have no local password
# ---------------------------------------------------------------------------


def _null_out_password(username: str) -> None:
    """Turn a signed-in account into an SSO-style one (no local password)."""
    from app.db import get_session_factory
    from app.models import AppUser

    with get_session_factory()() as db:
        u = db.query(AppUser).filter(AppUser.username == username).one()
        u.password_hash = None
        db.commit()


def test_sso_account_sees_notice_not_form(login_as) -> None:
    # Log in normally, then drop the local password — the auth dependency
    # reloads the user each request, so it is now an SSO-style account.
    c = login_as("view_only", username="sso_view")
    _null_out_password("sso_view")

    resp = c.get("/ui/account/password")
    assert resp.status_code == 200
    assert "identity provider" in resp.text.lower()
    # No password form is offered.
    assert 'name="current_password"' not in resp.text


def test_sso_account_cannot_post_password(login_as) -> None:
    c = login_as("view_only", username="sso_post")
    _null_out_password("sso_post")

    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": "anything",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert resp.status_code == 200
    assert "identity provider" in resp.text.lower()
    assert _password_hash("sso_post") is None  # still no local password


# ---------------------------------------------------------------------------
# Audit: the change is recorded, the value never is
# ---------------------------------------------------------------------------


def test_password_change_audited_without_leaking_value(login_as) -> None:
    secret = "myn3wsecretpw!"
    c = login_as("management", username="audit_user")
    resp = c.post(
        "/ui/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": secret,
            "confirm_password": secret,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.db import get_session_factory
    from app.models.audit_event import AuditEvent

    with get_session_factory()() as db:
        event = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.event_type == "app_user.password_changed",
                AuditEvent.target_label == "audit_user",
            )
            .one()
        )
        assert event.detail.get("self_service") is True
        assert secret not in (event.detail_json or "")
        assert secret not in (event.message or "")
