"""Static reference managers.

A *reference manager* is a stand-in supervisor record (the canonical example is
``margaretmanager``) that exists purely so real employees can be tagged with a
stable manager. It is deliberately hidden from every external/bulk read — the
API/MCP employee list, CSV export, and headcount/org reports — so downstream
systems (Saviynt and friends) never try to provision or update it. It still:

* resolves as the ``supervisor`` reference on employees who report to it
  (carrying ``employee_number`` — e.g. ``margaretmanager`` — with no spaces),
* is fetchable by id via ``GET /employees/{id}``, and
* shows in the app's own web UI, badged "Static" and locked from editing.

Two moving parts:

* :func:`exclude_reference_managers` — the one query filter every external read
  applies, so the hiding rule lives in exactly one place.
* :func:`ensure_seed_manager` — idempotently creates the seeded Margaret record
  from the values operators use on the Saviynt POC instances.

The per-row :attr:`Employee.is_reference_manager` flag is what actually hides a
record. The ``reference_managers_enabled`` toggle on the singleton app_config
(see :mod:`app.services.system_config`) only gates the UI affordances for
creating/marking them, so an operator opts the feature in per instance.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Query, Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import (
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    Location,
    StateProvince,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The seeded Margaret record
# ---------------------------------------------------------------------------

# Values mirror the operators' static Saviynt POC user. The employee_number is
# the space-free handle downstream systems key the manager on.
SEED_EMPLOYEE_NUMBER = "margaretmanager"
SEED_FIRST_NAME = "margaret"
SEED_LAST_NAME = "manager"
SEED_WORK_EMAIL = "margaretmanager@saviynt.com"
SEED_CITY = "El Segundo"
SEED_STATE_NAME = "California"
SEED_COUNTRY_CODE = "US"
SEED_HIRE_DATE = date(2020, 1, 1)
# Fitting home for a manager; falls back to any department/title if absent.
SEED_DEPARTMENT = "Human Resources"
SEED_JOB_TITLE = "HR Manager"


# ---------------------------------------------------------------------------
# Query filter — the single place the hiding rule lives
# ---------------------------------------------------------------------------


def is_not_reference_manager() -> ColumnElement[bool]:
    """SQL predicate matching only *real* (non-reference-manager) employees."""
    return Employee.is_reference_manager.is_(False)


def exclude_reference_managers(
    query: Query[Employee], *, include: bool = False
) -> Query[Employee]:
    """Filter reference managers out of an Employee query unless ``include``.

    Every external/bulk read (API list, MCP, CSV export, reports) funnels
    through here so the exclusion rule stays in one place.
    """
    if include:
        return query
    return query.filter(is_not_reference_manager())


# ---------------------------------------------------------------------------
# Feature toggle (thin wrappers over the app_config singleton)
# ---------------------------------------------------------------------------


def is_enabled(db: Session) -> bool:
    """True if the static-reference-manager feature is turned on for this instance."""
    from app.services import system_config

    return bool(system_config.get_config(db).reference_managers_enabled)


def set_enabled(db: Session, enabled: bool) -> None:
    """Persist the feature toggle and refresh the cached config."""
    from app.services import system_config

    row = system_config.get_config(db)
    row.reference_managers_enabled = enabled
    db.commit()
    system_config.invalidate()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def get_seed_manager(db: Session) -> Employee | None:
    """Return the seeded Margaret record if it already exists, else None."""
    return db.scalar(
        select(Employee).where(
            Employee.employee_number == SEED_EMPLOYEE_NUMBER
        )
    )


def ensure_seed_manager(db: Session) -> tuple[Employee | None, bool]:
    """Create the seeded ``margaretmanager`` reference record if missing.

    Idempotent. Returns ``(employee, created)`` — ``created`` is False when the
    record already existed. Returns ``(None, False)`` if the lookup data needed
    to satisfy the employee's required FKs isn't seeded yet.
    """
    existing = get_seed_manager(db)
    if existing is not None:
        # If an operator seeded Margaret before flagging existed, make sure the
        # flag is set so she's hidden from external reads.
        if not existing.is_reference_manager:
            existing.is_reference_manager = True
            db.commit()
        return existing, False

    active_status = db.scalar(
        select(EmploymentStatus).where(EmploymentStatus.label == "Active")
    )
    us_country = db.scalar(
        select(Country).where(Country.code == SEED_COUNTRY_CODE)
    )
    if not (active_status and us_country):
        log.warning("reference_manager_seed_prereqs_missing")
        return None, False

    department = db.scalar(
        select(Department).where(Department.name == SEED_DEPARTMENT)
    ) or db.scalar(select(Department).order_by(Department.id))
    if department is None:
        log.warning("reference_manager_seed_no_department")
        return None, False

    job_title = db.scalar(
        select(JobTitle).where(
            JobTitle.department_id == department.id,
            JobTitle.name == SEED_JOB_TITLE,
        )
    ) or db.scalar(
        select(JobTitle).where(JobTitle.department_id == department.id).order_by(
            JobTitle.id
        )
    )
    if job_title is None:
        log.warning("reference_manager_seed_no_job_title")
        return None, False

    # Optional bits — nice to have, never block the seed.
    california = db.scalar(
        select(StateProvince).where(
            StateProvince.country_id == us_country.id,
            StateProvince.name == SEED_STATE_NAME,
        )
    )
    el_segundo = db.scalar(
        select(Location).where(Location.name == SEED_CITY)
    )

    manager = Employee(
        employee_number=SEED_EMPLOYEE_NUMBER,
        first_name=SEED_FIRST_NAME,
        last_name=SEED_LAST_NAME,
        country_id=us_country.id,
        state_province_id=california.id if california else None,
        city=SEED_CITY,
        work_email=SEED_WORK_EMAIL,
        employment_status_id=active_status.id,
        department_id=department.id,
        job_title_id=job_title.id,
        location_id=el_segundo.id if el_segundo else None,
        hire_date=SEED_HIRE_DATE,
        supervisor_id=None,
        is_reference_manager=True,
        is_archived=False,
    )
    db.add(manager)
    db.commit()
    db.refresh(manager)
    log.info(
        "seeded_reference_manager",
        extra={"employee_number": SEED_EMPLOYEE_NUMBER},
    )
    return manager, True
