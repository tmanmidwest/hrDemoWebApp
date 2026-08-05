"""Tests for the admin-only 'delete all archived employees' cleanup route.

POST /ui/employees/purge-archived permanently deletes every archived employee.
It is admin-only and must survive the self-referential supervisor FK.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import get_session_factory
from app.models import (
    AppUser,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
)
from app.services.passwords import hash_password

PASSWORD = "verysecure123"


def _lookup_ids() -> dict[str, int]:
    """Return one id for each FK an Employee needs, from the seeded data."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        country = db.query(Country).first()
        dept = db.query(Department).first()
        title = db.query(JobTitle).filter(JobTitle.department_id == dept.id).first()
        status = db.query(EmploymentStatus).first()
        return {
            "country_id": country.id,
            "department_id": dept.id,
            "job_title_id": title.id,
            "employment_status_id": status.id,
        }


def _add_employee(number: str, *, archived: bool, supervisor_id: int | None = None) -> int:
    """Insert an employee directly and return its id."""
    ids = _lookup_ids()
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        emp = Employee(
            employee_number=number,
            first_name="Test",
            last_name=number,
            hire_date=date(2026, 1, 1),
            is_archived=archived,
            archived_at=datetime.now(UTC) if archived else None,
            supervisor_id=supervisor_id,
            **ids,
        )
        db.add(emp)
        db.commit()
        return emp.id


def _create_local_user(username: str, role: str) -> None:
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


@pytest.fixture
def login_as(client: TestClient) -> Callable[[str], TestClient]:
    def _login(role: str) -> TestClient:
        username = f"{role}_user"
        _create_local_user(username, role)
        resp = client.post(
            "/ui/login",
            data={"username": username, "password": PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        return client

    return _login


def _employee_numbers() -> set[str]:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        return {e.employee_number for e in db.query(Employee).all()}


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


def test_purge_deletes_only_archived(admin_client: TestClient) -> None:
    _add_employee("ARCH1", archived=True)
    _add_employee("ARCH2", archived=True)
    _add_employee("KEEP1", archived=False)

    resp = admin_client.post("/ui/employees/purge-archived", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/employees?view=archived"

    numbers = _employee_numbers()
    assert "ARCH1" not in numbers
    assert "ARCH2" not in numbers
    assert "KEEP1" in numbers


def test_purge_nulls_out_supervisor_references(admin_client: TestClient) -> None:
    """An active employee pointing at an archived supervisor must survive the
    purge with supervisor_id cleared — not fail on the self-referential FK."""
    boss_id = _add_employee("BOSS", archived=True)
    report_id = _add_employee("REPORT", archived=False, supervisor_id=boss_id)

    resp = admin_client.post("/ui/employees/purge-archived", follow_redirects=False)
    assert resp.status_code == 303

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        report = db.get(Employee, report_id)
        assert report is not None, "the active report should not be deleted"
        assert report.supervisor_id is None
        assert db.get(Employee, boss_id) is None, "archived supervisor is deleted"


def test_purge_with_no_archived_is_a_noop(admin_client: TestClient) -> None:
    before = _employee_numbers()
    resp = admin_client.post("/ui/employees/purge-archived", follow_redirects=False)
    assert resp.status_code == 303
    assert _employee_numbers() == before


def test_purge_writes_audit_event(admin_client: TestClient) -> None:
    _add_employee("ARCHX", archived=True)
    admin_client.post("/ui/employees/purge-archived", follow_redirects=False)

    from app.models import AuditEvent

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "employee.purged_archived")
            .one()
        )
        assert event.detail is not None
        assert "1" in event.message


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def _redirects_to_employees(resp) -> bool:
    return resp.status_code == 303 and resp.headers.get("location") == "/ui/employees"


def test_management_cannot_purge(login_as) -> None:
    _add_employee("ARCH_M", archived=True)
    c = login_as("management")
    resp = c.post("/ui/employees/purge-archived", follow_redirects=False)
    assert _redirects_to_employees(resp)
    assert "ARCH_M" in _employee_numbers(), "management must not delete anything"


def test_view_only_cannot_purge(login_as) -> None:
    _add_employee("ARCH_V", archived=True)
    c = login_as("view_only")
    resp = c.post("/ui/employees/purge-archived", follow_redirects=False)
    assert _redirects_to_employees(resp)
    assert "ARCH_V" in _employee_numbers()


def test_purge_requires_auth(client: TestClient) -> None:
    resp = client.post("/ui/employees/purge-archived", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# UI affordance
# ---------------------------------------------------------------------------


def test_delete_all_button_shows_for_admin_on_archived_tab(admin_client: TestClient) -> None:
    _add_employee("ARCH_UI", archived=True)
    body = admin_client.get("/ui/employees?view=archived").text
    assert "Delete all archived" in body
    assert "/ui/employees/purge-archived" in body


def test_delete_all_button_hidden_on_active_tab(admin_client: TestClient) -> None:
    _add_employee("ARCH_UI2", archived=True)
    body = admin_client.get("/ui/employees?view=active").text
    assert "purge-archived" not in body


def test_delete_all_button_hidden_for_management(login_as) -> None:
    _add_employee("ARCH_UI3", archived=True)
    c = login_as("management")
    body = c.get("/ui/employees?view=archived").text
    assert "purge-archived" not in body
