"""Tests for the employee CSV import / export UI.

Walks a logged-in admin through the download → upload → preview → commit flow
and asserts classification, blank-cell semantics, supervisor forward-refs, and
that audit events land in the Activity Log.
"""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.services import employee_import


@pytest.fixture
def ui_session(client: TestClient) -> TestClient:
    """Log in via the HTML login form; returns the client with the cookie set."""
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
    assert resp.status_code == 303, resp.text
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_csv(rows: list[dict[str, str]]) -> str:
    """Build a full-width CSV (all columns) from partial row dicts."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=employee_import.COLUMNS, extrasaction="ignore"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in employee_import.COLUMNS})
    return buffer.getvalue()


def _valid_row(**overrides: str) -> dict[str, str]:
    """A minimally-valid NEW employee row using seeded lookup names."""
    base = {
        "employee_number": "E90001",
        "first_name": "Jamie",
        "last_name": "Lee",
        "country": "United States",
        "employment_status": "Active",
        "department": "Engineering",
        "job_title": "Software Engineer",
        "hire_date": "2026-02-01",
    }
    base.update(overrides)
    return base


def _preview(client: TestClient, csv_text: str):
    """POST a CSV to the preview endpoint and return the response."""
    return client.post(
        "/ui/employees/import/preview",
        files={"file": ("import.csv", csv_text.encode("utf-8"), "text/csv")},
    )


def _commit(client: TestClient, csv_text: str):
    return client.post(
        "/ui/employees/import/commit",
        data={"csv_text": csv_text},
        follow_redirects=False,
    )


def _get_employee(employee_number: str):
    """Fetch an employee straight from the DB for assertions."""
    from app.db import get_session_factory
    from app.models import Employee

    session = get_session_factory()()
    try:
        return (
            session.query(Employee)
            .filter(Employee.employee_number == employee_number)
            .first()
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def test_template_download(ui_session: TestClient) -> None:
    resp = ui_session.get("/ui/employees/import/template.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    header = resp.text.splitlines()[0]
    assert header.split(",") == employee_import.COLUMNS


def test_export_has_import_shape_and_no_ssn(ui_session: TestClient) -> None:
    resp = ui_session.get("/ui/employees/export.csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    assert reader.fieldnames == employee_import.COLUMNS
    for row in reader:
        assert row["ssn"] == ""  # SSN is never exported


# ---------------------------------------------------------------------------
# Preview classification
# ---------------------------------------------------------------------------


def test_preview_classifies_new_and_error(ui_session: TestClient) -> None:
    csv_text = _make_csv(
        [
            _valid_row(employee_number="E90001"),
            # Missing last_name and an unknown department → error row.
            {
                "employee_number": "E90002",
                "first_name": "Pat",
                "country": "United States",
                "employment_status": "Active",
                "department": "Nonexistent Dept",
                "job_title": "Software Engineer",
                "hire_date": "2026-02-01",
            },
        ]
    )
    resp = _preview(ui_session, csv_text)
    assert resp.status_code == 200
    assert "1 new" in resp.text
    assert "1 error" in resp.text
    assert "Last name is required" in resp.text
    assert "Nonexistent Dept" in resp.text


def test_preview_detects_update_vs_new(ui_session: TestClient) -> None:
    _commit(ui_session, _make_csv([_valid_row(employee_number="E90050")]))
    # Same number again with a changed name → Update.
    resp = _preview(
        ui_session,
        _make_csv([_valid_row(employee_number="E90050", last_name="Changed")]),
    )
    assert resp.status_code == 200
    assert "1 updated" in resp.text
    assert "Last name" in resp.text  # changed-field hint


# ---------------------------------------------------------------------------
# Commit: create / update / blank-keeps-existing
# ---------------------------------------------------------------------------


def test_commit_creates_employee(ui_session: TestClient) -> None:
    resp = _commit(
        ui_session,
        _make_csv([_valid_row(employee_number="E90100", work_email="j@company.com")]),
    )
    assert resp.status_code == 303
    emp = _get_employee("E90100")
    assert emp is not None
    assert emp.first_name == "Jamie"
    assert emp.work_email == "j@company.com"


def test_update_blank_cell_keeps_existing(ui_session: TestClient) -> None:
    # Create with a work email...
    _commit(
        ui_session,
        _make_csv(
            [_valid_row(employee_number="E90200", work_email="keep@company.com")]
        ),
    )
    # ...then update with the work_email column left blank → unchanged.
    _commit(
        ui_session,
        _make_csv([_valid_row(employee_number="E90200", last_name="Newname")]),
    )
    emp = _get_employee("E90200")
    assert emp is not None
    assert emp.last_name == "Newname"  # changed
    assert emp.work_email == "keep@company.com"  # preserved


def test_supervisor_forward_reference(ui_session: TestClient) -> None:
    # Row 2 references row 1 as supervisor; row 1 appears *after* is irrelevant —
    # here it's before, but the point is it's a same-file (not-yet-in-DB) ref.
    csv_text = _make_csv(
        [
            _valid_row(employee_number="E90300", first_name="Boss", last_name="One"),
            _valid_row(
                employee_number="E90301",
                first_name="Report",
                last_name="Two",
                supervisor_employee_number="E90300",
            ),
        ]
    )
    resp = _commit(ui_session, csv_text)
    assert resp.status_code == 303
    boss = _get_employee("E90300")
    report = _get_employee("E90301")
    assert boss is not None and report is not None
    assert report.supervisor_id == boss.id


def test_unknown_supervisor_is_error(ui_session: TestClient) -> None:
    resp = _preview(
        ui_session,
        _make_csv(
            [_valid_row(employee_number="E90400", supervisor_employee_number="E99999")]
        ),
    )
    assert resp.status_code == 200
    assert "1 error" in resp.text
    assert "E99999" in resp.text


def test_commit_skips_errors_imports_valid(ui_session: TestClient) -> None:
    csv_text = _make_csv(
        [
            _valid_row(employee_number="E90500"),
            {"employee_number": "E90501", "first_name": "NoDept"},  # errors
        ]
    )
    resp = _commit(ui_session, csv_text)
    assert resp.status_code == 303
    assert _get_employee("E90500") is not None  # valid one imported
    assert _get_employee("E90501") is None  # errored one skipped


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_import_records_audit_events(ui_session: TestClient) -> None:
    _commit(ui_session, _make_csv([_valid_row(employee_number="E90600")]))
    resp = ui_session.get("/ui/activity")
    assert resp.status_code == 200
    # Summary + per-row events should be visible in the Activity log.
    assert "Imported employees" in resp.text
    assert "E90600" in resp.text


def test_export_records_audit_event(ui_session: TestClient) -> None:
    ui_session.get("/ui/employees/export.csv")
    resp = ui_session.get("/ui/activity")
    assert "Exported employees to CSV" in resp.text


# ---------------------------------------------------------------------------
# Permission / auth gate
# ---------------------------------------------------------------------------


def test_import_requires_login(client: TestClient) -> None:
    resp = client.get("/ui/employees/import", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/ui/login" in resp.headers["location"]
