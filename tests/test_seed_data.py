"""Tests for the seed_data service."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_session_factory
from app.models import (
    AppUser,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    StateProvince,
)
from app.services.migrations import run_migrations
from app.services.passwords import verify_password
from app.services.seed_data import (
    reset_admin_password,
    reset_employees,
    seed_database,
)


@pytest.fixture
def seeded_db() -> None:
    """Migrate and seed a fresh DB."""
    run_migrations()
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        seed_database(db, get_settings())


def test_seed_populates_countries(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Country))
        assert count is not None and count >= 50  # at least the major ones
        # Spot check
        us = db.scalar(select(Country).where(Country.code == "US"))
        assert us is not None
        assert us.name == "United States"


def test_seed_populates_states_for_us(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        us = db.scalar(select(Country).where(Country.code == "US"))
        assert us is not None
        state_count = db.scalar(
            select(func.count()).select_from(StateProvince).where(
                StateProvince.country_id == us.id
            )
        )
        assert state_count == 51  # 50 states + DC


def test_seed_populates_employment_statuses(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        statuses = db.scalars(select(EmploymentStatus)).all()
        labels = {s.label for s in statuses}
        assert labels == {"Active", "Not Active", "Leave of Absence", "Terminated"}

        active = next(s for s in statuses if s.label == "Active")
        assert active.value == 1
        assert active.is_active_status is True
        assert active.is_system is True

        not_active = next(s for s in statuses if s.label == "Not Active")
        assert not_active.value == 0
        assert not_active.is_active_status is False
        assert not_active.is_system is True


def test_seed_populates_departments_and_titles(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        dept_count = db.scalar(select(func.count()).select_from(Department))
        title_count = db.scalar(select(func.count()).select_from(JobTitle))
        assert dept_count is not None and dept_count >= 5
        assert title_count is not None and title_count >= 15

        # Verify a specific department has its titles
        eng = db.scalar(select(Department).where(Department.name == "Engineering"))
        assert eng is not None
        eng_titles = {t.name for t in eng.job_titles}
        assert "Software Engineer" in eng_titles
        assert "Senior Software Engineer" in eng_titles


def test_seed_creates_admin_user_with_working_password(seeded_db: None) -> None:
    settings = get_settings()
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        admin = db.scalar(
            select(AppUser).where(AppUser.username == settings.initial_admin_username)
        )
        assert admin is not None
        assert admin.is_seeded is True
        assert admin.is_active is True
        assert verify_password(settings.initial_admin_password, admin.password_hash)
        assert not verify_password("wrong-password", admin.password_hash)


def test_seed_creates_sample_employees(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        employees = db.scalars(select(Employee)).all()
        assert len(employees) == 2

        # First one has no supervisor
        boss = next(e for e in employees if e.employee_number == "E00001")
        assert boss.supervisor_id is None

        # Second is supervised by the first
        report = next(e for e in employees if e.employee_number == "E00002")
        assert report.supervisor_id == boss.id

        # Both are 'Not Active' per spec
        for emp in employees:
            assert emp.employment_status.label == "Not Active"
            assert emp.employment_status.is_active_status is False


def test_seed_writes_initial_credentials_file(seeded_db: None) -> None:
    settings = get_settings()
    assert settings.initial_credentials_path.exists()
    content = settings.initial_credentials_path.read_text()
    assert settings.initial_admin_username in content
    assert settings.initial_admin_password in content


def test_seed_is_idempotent(seeded_db: None) -> None:
    """Running seed_database again should not duplicate any rows."""
    SessionLocal = get_session_factory()

    with SessionLocal() as db:
        before = {
            "countries": db.scalar(select(func.count()).select_from(Country)),
            "states": db.scalar(select(func.count()).select_from(StateProvince)),
            "statuses": db.scalar(select(func.count()).select_from(EmploymentStatus)),
            "depts": db.scalar(select(func.count()).select_from(Department)),
            "titles": db.scalar(select(func.count()).select_from(JobTitle)),
            "employees": db.scalar(select(func.count()).select_from(Employee)),
            "users": db.scalar(select(func.count()).select_from(AppUser)),
        }

    with SessionLocal() as db:
        seed_database(db, get_settings())

    with SessionLocal() as db:
        after = {
            "countries": db.scalar(select(func.count()).select_from(Country)),
            "states": db.scalar(select(func.count()).select_from(StateProvince)),
            "statuses": db.scalar(select(func.count()).select_from(EmploymentStatus)),
            "depts": db.scalar(select(func.count()).select_from(Department)),
            "titles": db.scalar(select(func.count()).select_from(JobTitle)),
            "employees": db.scalar(select(func.count()).select_from(Employee)),
            "users": db.scalar(select(func.count()).select_from(AppUser)),
        }

    assert before == after


def test_reset_employees_clears_and_reseeds(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        deleted = reset_employees(db, reseed_samples=True)
        assert deleted == 2

    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Employee))
        assert count == 2  # Sample employees re-seeded


def test_reset_employees_without_reseed(seeded_db: None) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        deleted = reset_employees(db, reseed_samples=False)
        assert deleted == 2

    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Employee))
        assert count == 0


def test_reset_admin_password_restores_default(seeded_db: None) -> None:
    settings = get_settings()
    SessionLocal = get_session_factory()

    # Corrupt the password
    with SessionLocal() as db:
        admin = db.scalar(
            select(AppUser).where(AppUser.username == settings.initial_admin_username)
        )
        assert admin is not None
        admin.password_hash = "corrupted"
        admin.is_active = False
        db.commit()

    # Reset
    with SessionLocal() as db:
        success = reset_admin_password(db, settings)
        assert success is True

    # Verify
    with SessionLocal() as db:
        admin = db.scalar(
            select(AppUser).where(AppUser.username == settings.initial_admin_username)
        )
        assert admin is not None
        assert admin.is_active is True
        assert verify_password(settings.initial_admin_password, admin.password_hash)


def test_reset_admin_password_does_not_touch_other_admins(seeded_db: None) -> None:
    """Reset should leave non-seeded admins alone."""
    from app.services.passwords import hash_password

    SessionLocal = get_session_factory()

    # Create a second admin (not seeded)
    with SessionLocal() as db:
        other = AppUser(
            username="another_admin",
            password_hash=hash_password("their-own-password"),
            is_seeded=False,
            is_active=True,
        )
        db.add(other)
        db.commit()
        other_hash_before = other.password_hash

    # Reset the seeded admin
    with SessionLocal() as db:
        reset_admin_password(db, get_settings())

    # Other admin should be untouched
    with SessionLocal() as db:
        other = db.scalar(select(AppUser).where(AppUser.username == "another_admin"))
        assert other is not None
        assert other.password_hash == other_hash_before
        assert verify_password("their-own-password", other.password_hash)
