"""Seed data loader.

The seed_database() function is idempotent: it can be safely called on every
startup. It only inserts rows that are missing, never updates or deletes
existing data.

Individual reset functions (reset_employees, reset_lookup_tables, etc.) are
exposed for the reset UI to use.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AppUser,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    StateProvince,
)
from app.services._seed_countries import COUNTRIES
from app.services._seed_states import STATES_PROVINCES
from app.services.passwords import hash_password

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default employment statuses
# ---------------------------------------------------------------------------

# (label, value, is_active_status, is_system)
DEFAULT_EMPLOYMENT_STATUSES: list[tuple[str, int, bool, bool]] = [
    ("Active", 1, True, True),
    ("Not Active", 0, False, True),
    ("Leave of Absence", 2, True, False),
    ("Terminated", 3, False, False),
]


# ---------------------------------------------------------------------------
# Default departments and their job titles
# ---------------------------------------------------------------------------

# department_name -> list of job titles
DEFAULT_DEPARTMENTS: dict[str, list[str]] = {
    "Engineering": [
        "Software Engineer",
        "Senior Software Engineer",
        "Staff Engineer",
        "Engineering Manager",
        "VP of Engineering",
    ],
    "Sales": [
        "Sales Development Representative",
        "Account Executive",
        "Senior Account Executive",
        "Sales Manager",
        "VP of Sales",
    ],
    "Marketing": [
        "Marketing Specialist",
        "Marketing Manager",
        "Content Strategist",
        "VP of Marketing",
    ],
    "Human Resources": [
        "HR Generalist",
        "HR Business Partner",
        "Recruiter",
        "HR Manager",
        "VP of Human Resources",
    ],
    "Finance": [
        "Financial Analyst",
        "Senior Financial Analyst",
        "Accounting Manager",
        "Controller",
        "CFO",
    ],
    "Operations": [
        "Operations Analyst",
        "Operations Manager",
        "VP of Operations",
    ],
    "IT": [
        "IT Support Specialist",
        "Systems Administrator",
        "DevOps Engineer",
        "IT Manager",
        "CIO",
    ],
    "Customer Support": [
        "Support Specialist",
        "Senior Support Specialist",
        "Support Manager",
    ],
}


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def seed_database(db: Session, settings: Settings | None = None) -> None:
    """Idempotently seed all default data.

    Safe to call on every startup. Existing rows are left untouched.
    """
    settings = settings or get_settings()

    seed_countries(db)
    seed_states_provinces(db)
    seed_employment_statuses(db)
    seed_departments_and_titles(db)
    seed_admin_user(db, settings)
    seed_sample_employees(db)

    db.commit()

    # Summary log with current row counts — makes ops verification easy
    from sqlalchemy import func

    from app.models import ApiKey, OAuthClient

    log.info(
        "seed_database_complete",
        extra={
            "countries": db.scalar(select(func.count()).select_from(Country)) or 0,
            "states_provinces": db.scalar(
                select(func.count()).select_from(StateProvince)
            )
            or 0,
            "employment_statuses": db.scalar(
                select(func.count()).select_from(EmploymentStatus)
            )
            or 0,
            "departments": db.scalar(select(func.count()).select_from(Department)) or 0,
            "job_titles": db.scalar(select(func.count()).select_from(JobTitle)) or 0,
            "employees": db.scalar(select(func.count()).select_from(Employee)) or 0,
            "app_users": db.scalar(select(func.count()).select_from(AppUser)) or 0,
            "api_keys": db.scalar(select(func.count()).select_from(ApiKey)) or 0,
            "oauth_clients": db.scalar(select(func.count()).select_from(OAuthClient)) or 0,
        },
    )


# ---------------------------------------------------------------------------
# Individual seeders
# ---------------------------------------------------------------------------


def seed_countries(db: Session) -> int:
    """Insert any missing countries. Returns number of rows inserted."""
    existing_codes = {row[0] for row in db.execute(select(Country.code)).all()}
    inserted = 0
    for code, name in COUNTRIES:
        if code not in existing_codes:
            db.add(Country(code=code, name=name, is_active=True))
            inserted += 1
    if inserted:
        db.flush()
        log.info("seeded_countries", extra={"inserted": inserted})
    return inserted


def seed_states_provinces(db: Session) -> int:
    """Insert any missing states/provinces. Returns number of rows inserted."""
    # Need country IDs to link by
    countries_by_code = {c.code: c.id for c in db.scalars(select(Country)).all()}
    # Existing (country_id, name) pairs
    existing = {
        (row[0], row[1])
        for row in db.execute(
            select(StateProvince.country_id, StateProvince.name)
        ).all()
    }
    inserted = 0
    for country_code, sub_code, name in STATES_PROVINCES:
        country_id = countries_by_code.get(country_code)
        if country_id is None:
            continue  # parent country not seeded (filtered out)
        if (country_id, name) in existing:
            continue
        db.add(
            StateProvince(
                country_id=country_id,
                code=sub_code,
                name=name,
                is_active=True,
            )
        )
        inserted += 1
    if inserted:
        db.flush()
        log.info("seeded_states_provinces", extra={"inserted": inserted})
    return inserted


def seed_employment_statuses(db: Session) -> int:
    """Insert any missing employment statuses. Returns number of rows inserted."""
    existing_labels = {
        row[0] for row in db.execute(select(EmploymentStatus.label)).all()
    }
    inserted = 0
    for label, value, is_active, is_system in DEFAULT_EMPLOYMENT_STATUSES:
        if label not in existing_labels:
            db.add(
                EmploymentStatus(
                    label=label,
                    value=value,
                    is_active_status=is_active,
                    is_system=is_system,
                )
            )
            inserted += 1
    if inserted:
        db.flush()
        log.info("seeded_employment_statuses", extra={"inserted": inserted})
    return inserted


def seed_departments_and_titles(db: Session) -> tuple[int, int]:
    """Insert any missing departments and job titles. Returns (depts, titles) inserted."""
    existing_depts = {d.name: d for d in db.scalars(select(Department)).all()}

    depts_inserted = 0
    for dept_name in DEFAULT_DEPARTMENTS:
        if dept_name not in existing_depts:
            dept = Department(name=dept_name, is_active=True)
            db.add(dept)
            existing_depts[dept_name] = dept
            depts_inserted += 1
    if depts_inserted:
        db.flush()

    # Now job titles
    existing_titles = {
        (row[0], row[1])
        for row in db.execute(select(JobTitle.department_id, JobTitle.name)).all()
    }
    titles_inserted = 0
    for dept_name, titles in DEFAULT_DEPARTMENTS.items():
        dept = existing_depts[dept_name]
        for title in titles:
            if (dept.id, title) in existing_titles:
                continue
            db.add(JobTitle(department_id=dept.id, name=title, is_active=True))
            titles_inserted += 1
    if titles_inserted:
        db.flush()
        log.info(
            "seeded_departments_and_titles",
            extra={"departments_inserted": depts_inserted, "titles_inserted": titles_inserted},
        )
    return depts_inserted, titles_inserted


def seed_admin_user(db: Session, settings: Settings) -> bool:
    """Create the seeded admin user if it doesn't exist.

    Returns True if the user was created, False if it already existed.
    Also writes /data/INITIAL_CREDENTIALS.txt if the user is newly created.
    """
    username = settings.initial_admin_username
    existing = db.scalar(select(AppUser).where(AppUser.username == username))
    if existing is not None:
        return False

    user = AppUser(
        username=username,
        password_hash=hash_password(settings.initial_admin_password),
        is_seeded=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    log.info("seeded_admin_user", extra={"username": username})

    # Write initial credentials file for operator reference
    write_initial_credentials_file(settings)

    return True


def write_initial_credentials_file(settings: Settings) -> None:
    """Write the INITIAL_CREDENTIALS.txt file for operator reference."""
    settings.ensure_data_dir()
    content = (
        f"Demo HR Source of Truth App — Initial Credentials\n"
        f"{'=' * 50}\n"
        f"\n"
        f"Web UI: http://<your-host>:{settings.bind_port}\n"
        f"\n"
        f"Username: {settings.initial_admin_username}\n"
        f"Password: {settings.initial_admin_password}\n"
        f"\n"
        f"WARNING: This is a non-production POC application.\n"
        f"Change this password immediately via the UI for any non-trivial use.\n"
        f"Delete this file after initial setup.\n"
    )
    settings.initial_credentials_path.write_text(content)
    try:
        settings.initial_credentials_path.chmod(0o600)
    except OSError:
        # Filesystem may not support chmod (e.g., Windows-mounted volumes)
        pass


def seed_sample_employees(db: Session) -> int:
    """Seed two sample employees if the table is empty.

    The first has no supervisor; the second is supervised by the first.
    Both are seeded with 'Not Active' employment status so they're inert
    until the operator activates them.

    Returns the number of employees inserted (0 if table was non-empty).
    """
    existing_count = db.scalar(select(Employee.id).limit(1))
    if existing_count is not None:
        return 0

    # Look up FKs
    not_active_status = db.scalar(
        select(EmploymentStatus).where(EmploymentStatus.label == "Not Active")
    )
    us_country = db.scalar(select(Country).where(Country.code == "US"))
    engineering = db.scalar(select(Department).where(Department.name == "Engineering"))
    if not (not_active_status and us_country and engineering):
        log.warning("sample_employee_seed_prereqs_missing")
        return 0

    senior_title = db.scalar(
        select(JobTitle).where(
            JobTitle.department_id == engineering.id,
            JobTitle.name == "Senior Software Engineer",
        )
    )
    junior_title = db.scalar(
        select(JobTitle).where(
            JobTitle.department_id == engineering.id,
            JobTitle.name == "Software Engineer",
        )
    )
    if not (senior_title and junior_title):
        log.warning("sample_employee_seed_job_titles_missing")
        return 0

    # First employee — no supervisor
    boss = Employee(
        employee_number="E00001",
        first_name="Sample",
        middle_name=None,
        last_name="Manager",
        country_id=us_country.id,
        work_email="sample.manager@example.com",
        employment_status_id=not_active_status.id,
        department_id=engineering.id,
        job_title_id=senior_title.id,
        hire_date=date(2024, 1, 15),
        supervisor_id=None,
        is_archived=False,
    )
    db.add(boss)
    db.flush()

    # Second employee — supervised by the first
    db.add(
        Employee(
            employee_number="E00002",
            first_name="Sample",
            middle_name=None,
            last_name="Employee",
            country_id=us_country.id,
            work_email="sample.employee@example.com",
            employment_status_id=not_active_status.id,
            department_id=engineering.id,
            job_title_id=junior_title.id,
            hire_date=date(2025, 3, 1),
            supervisor_id=boss.id,
            is_archived=False,
        )
    )
    db.flush()
    log.info("seeded_sample_employees", extra={"count": 2})
    return 2


# ---------------------------------------------------------------------------
# Reset operations (used by the UI reset feature later)
# ---------------------------------------------------------------------------


def reset_employees(db: Session, reseed_samples: bool = True) -> int:
    """Delete all employees and optionally reseed sample data.

    Returns the number of employees that were deleted.
    """
    # Null out supervisor_ids first to satisfy FK constraints
    db.query(Employee).update({Employee.supervisor_id: None})
    db.flush()
    deleted = db.query(Employee).delete()
    db.flush()
    log.info("reset_employees", extra={"deleted": deleted})

    if reseed_samples:
        seed_sample_employees(db)

    db.commit()
    return deleted


def reset_countries(db: Session) -> int:
    """Delete all countries (and cascade to states), then reseed.

    Warning: this will fail if any employees still reference these countries.
    The caller should reset employees first if needed.

    Returns the number of countries inserted after reset.
    """
    # Delete states first (CASCADE should handle this, but be explicit)
    db.query(StateProvince).delete()
    db.query(Country).delete()
    db.flush()
    inserted = seed_countries(db)
    seed_states_provinces(db)
    db.commit()
    return inserted


def reset_states_provinces(db: Session) -> int:
    """Delete all states/provinces and reseed."""
    db.query(StateProvince).delete()
    db.flush()
    inserted = seed_states_provinces(db)
    db.commit()
    return inserted


def reset_employment_statuses(db: Session) -> int:
    """Delete all employment statuses and reseed.

    Warning: fails if any employees still reference these statuses.
    """
    db.query(EmploymentStatus).delete()
    db.flush()
    inserted = seed_employment_statuses(db)
    db.commit()
    return inserted


def reset_departments_and_titles(db: Session) -> tuple[int, int]:
    """Delete all departments and job titles, then reseed.

    Warning: fails if any employees still reference these departments/titles.
    """
    db.query(JobTitle).delete()
    db.query(Department).delete()
    db.flush()
    result = seed_departments_and_titles(db)
    db.commit()
    return result


def reset_admin_password(db: Session, settings: Settings | None = None) -> bool:
    """Reset the seeded admin user's password back to the configured default.

    Returns True if the user was found and reset, False if not found.
    Does not touch other admin users.
    """
    settings = settings or get_settings()
    username = settings.initial_admin_username
    user = db.scalar(select(AppUser).where(AppUser.username == username))
    if user is None or not user.is_seeded:
        return False
    user.password_hash = hash_password(settings.initial_admin_password)
    user.is_active = True
    user.updated_at = datetime.now(UTC)
    db.commit()
    log.info("reset_admin_password", extra={"username": username})
    return True
