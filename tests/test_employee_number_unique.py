"""Tests for the pre-commit employee-number uniqueness validator.

The `employee_number` column is DB-unique, but that only surfaces as an
IntegrityError after commit. `validate_employee_number_unique` gives the UI a
friendly, case-insensitive check before the insert is attempted.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app.db import get_session_factory
from app.models import Country, Department, Employee, EmploymentStatus, JobTitle
from app.services.employee_validation import validate_employee_number_unique
from app.services.migrations import run_migrations


@pytest.fixture
def seeded_employee() -> int:
    """Create the lookups + one employee 'E00001'. Returns that employee's id."""
    run_migrations()
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        country = Country(code="US", name="United States", is_active=True)
        dept = Department(name="Engineering", is_active=True)
        db.add_all([country, dept])
        db.flush()
        title = JobTitle(department_id=dept.id, name="Engineer", is_active=True)
        status = EmploymentStatus(
            label="Active", value=1, is_active_status=True, is_system=True
        )
        db.add_all([title, status])
        db.flush()
        emp = Employee(
            employee_number="E00001",
            first_name="Ada",
            last_name="Lovelace",
            country_id=country.id,
            employment_status_id=status.id,
            department_id=dept.id,
            job_title_id=title.id,
            hire_date=date(2026, 1, 1),
        )
        db.add(emp)
        db.commit()
        return emp.id


def test_duplicate_number_rejected(seeded_employee: int) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            validate_employee_number_unique(db, "E00001")
    assert exc.value.status_code == 400
    assert "already in use" in exc.value.detail


def test_duplicate_number_is_case_insensitive(seeded_employee: int) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        with pytest.raises(HTTPException):
            validate_employee_number_unique(db, "e00001")


def test_surrounding_whitespace_still_collides(seeded_employee: int) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        with pytest.raises(HTTPException):
            validate_employee_number_unique(db, "  E00001  ")


def test_fresh_number_passes(seeded_employee: int) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        # Does not raise.
        validate_employee_number_unique(db, "E99999")


def test_excluding_self_allows_same_number(seeded_employee: int) -> None:
    """On edit, an employee keeping its own number must not collide with itself."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        validate_employee_number_unique(
            db, "E00001", excluding_employee_id=seeded_employee
        )
