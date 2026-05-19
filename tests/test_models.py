"""Tests for database models and migrations."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import get_engine, get_session_factory
from app.services.migrations import run_migrations


@pytest.fixture
def migrated_db() -> None:
    """Run migrations on a fresh DB. Fixture has no return value."""
    run_migrations()


def test_migrations_create_all_expected_tables(migrated_db: None) -> None:
    """Verify the initial migration creates every model's table."""
    engine = get_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "app_users",
        "api_keys",
        "countries",
        "departments",
        "employees",
        "employment_statuses",
        "job_titles",
        "oauth_clients",
        "states_provinces",
        # plus alembic_version
        "alembic_version",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_employees_have_self_supervision_check_constraint(migrated_db: None) -> None:
    """An employee cannot be their own supervisor."""
    from datetime import date

    from app.models import Country, Department, Employee, EmploymentStatus, JobTitle

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        # Seed minimal prerequisites
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

        # Create the employee
        emp = Employee(
            employee_number="E1",
            first_name="Test",
            last_name="Employee",
            country_id=country.id,
            employment_status_id=status.id,
            department_id=dept.id,
            job_title_id=title.id,
            hire_date=date(2026, 1, 1),
            supervisor_id=None,
        )
        db.add(emp)
        db.commit()

        # Try to make them their own supervisor
        emp.supervisor_id = emp.id
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_employee_number_is_unique(migrated_db: None) -> None:
    """Cannot create two employees with the same employee_number."""
    from datetime import date

    from app.models import Country, Department, Employee, EmploymentStatus, JobTitle

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

        common_kwargs = {
            "first_name": "A",
            "last_name": "B",
            "country_id": country.id,
            "employment_status_id": status.id,
            "department_id": dept.id,
            "job_title_id": title.id,
            "hire_date": date(2026, 1, 1),
        }
        db.add(Employee(employee_number="DUP1", **common_kwargs))
        db.commit()

        db.add(Employee(employee_number="DUP1", **common_kwargs))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_supervisor_relationship_loads(migrated_db: None) -> None:
    """The supervisor relationship resolves to another Employee object."""
    from datetime import date

    from app.models import Country, Department, Employee, EmploymentStatus, JobTitle

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

        boss = Employee(
            employee_number="BOSS1",
            first_name="Big",
            last_name="Boss",
            country_id=country.id,
            employment_status_id=status.id,
            department_id=dept.id,
            job_title_id=title.id,
            hire_date=date(2024, 1, 1),
        )
        db.add(boss)
        db.flush()

        report = Employee(
            employee_number="REP1",
            first_name="Direct",
            last_name="Report",
            country_id=country.id,
            employment_status_id=status.id,
            department_id=dept.id,
            job_title_id=title.id,
            hire_date=date(2025, 1, 1),
            supervisor_id=boss.id,
        )
        db.add(report)
        db.commit()

        # Reload and verify the relationships
        loaded = db.scalar(select(Employee).where(Employee.employee_number == "REP1"))
        assert loaded is not None
        assert loaded.supervisor is not None
        assert loaded.supervisor.employee_number == "BOSS1"
        assert len(loaded.supervisor.direct_reports) == 1
