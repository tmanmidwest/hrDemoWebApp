"""Cross-FK validation for employees.

These rules can't be expressed as simple column constraints because they
involve relationships between two different FK fields. Both create and update
flows run these checks.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    Location,
    StateProvince,
)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=message,
    )


def validate_country_id(db: Session, country_id: int) -> Country:
    """Resolve and return the country, or raise 400."""
    country = db.get(Country, country_id)
    if country is None:
        raise _bad_request(f"country_id {country_id} does not exist.")
    return country


def validate_state_belongs_to_country(
    db: Session, state_province_id: int, country_id: int
) -> StateProvince:
    """Resolve and return the state, or raise 400 if it doesn't belong to the country."""
    state = db.get(StateProvince, state_province_id)
    if state is None:
        raise _bad_request(f"state_province_id {state_province_id} does not exist.")
    if state.country_id != country_id:
        raise _bad_request(
            f"state_province_id {state_province_id} ('{state.name}') does not "
            f"belong to country_id {country_id}."
        )
    return state


def validate_employment_status(
    db: Session, employment_status_id: int
) -> EmploymentStatus:
    s = db.get(EmploymentStatus, employment_status_id)
    if s is None:
        raise _bad_request(
            f"employment_status_id {employment_status_id} does not exist."
        )
    return s


def resolve_employment_status_by_value(
    db: Session, value: int
) -> EmploymentStatus:
    """Look up an employment status by its numeric `value` (the IGA-facing code).

    Saviynt and other IGA platforms store status as a stable numeric code (e.g.,
    1 = Active, 0 = Not Active, 3 = Terminated) — not the DB primary key, which
    can shift across deployments. This lets the API accept that stable code on
    writes.

    The `value` column is not currently DB-unique, so this defensively rejects
    the ambiguous "multiple statuses share this value" case rather than picking
    one silently.
    """
    matches = (
        db.query(EmploymentStatus)
        .filter(EmploymentStatus.value == value)
        .all()
    )
    if not matches:
        raise _bad_request(
            f"No employment status exists with value={value}. "
            "Known seeded values: 1=Active, 0=Not Active, 2=Leave of Absence, 3=Terminated."
        )
    if len(matches) > 1:
        labels = ", ".join(f"'{m.label}'" for m in matches)
        raise _bad_request(
            f"Ambiguous: multiple employment statuses share value={value} ({labels}). "
            "Use employment_status_id instead, or deduplicate the statuses."
        )
    return matches[0]


def validate_department(db: Session, department_id: int) -> Department:
    d = db.get(Department, department_id)
    if d is None:
        raise _bad_request(f"department_id {department_id} does not exist.")
    return d


def validate_location(db: Session, location_id: int) -> Location:
    """Resolve and return the location, or raise 400.

    Only call this when location_id is not None — location is optional.
    """
    loc = db.get(Location, location_id)
    if loc is None:
        raise _bad_request(f"location_id {location_id} does not exist.")
    return loc


def validate_job_title(db: Session, job_title_id: int) -> JobTitle:
    """Resolve and return the job title, or raise 400. Existence only.

    Used when a job title is set without a department (both are optional); when a
    department is also present, use
    :func:`validate_job_title_belongs_to_department` for the stronger check.
    """
    title = db.get(JobTitle, job_title_id)
    if title is None:
        raise _bad_request(f"job_title_id {job_title_id} does not exist.")
    return title


def validate_job_title_belongs_to_department(
    db: Session, job_title_id: int, department_id: int
) -> JobTitle:
    title = db.get(JobTitle, job_title_id)
    if title is None:
        raise _bad_request(f"job_title_id {job_title_id} does not exist.")
    if title.department_id != department_id:
        raise _bad_request(
            f"job_title_id {job_title_id} ('{title.name}') does not belong to "
            f"department_id {department_id}."
        )
    return title


def validate_supervisor(
    db: Session,
    supervisor_id: int,
    excluding_employee_id: int | None = None,
) -> Employee:
    """Validate that a supervisor candidate is:
    - An existing employee
    - Not the employee being edited (no self-supervision)
    - Currently active (employment_status.is_active_status == True)
    - Not archived
    """
    supervisor = db.get(Employee, supervisor_id)
    if supervisor is None:
        raise _bad_request(f"supervisor_id {supervisor_id} does not exist.")
    if excluding_employee_id is not None and supervisor.id == excluding_employee_id:
        raise _bad_request("An employee cannot be their own supervisor.")
    if supervisor.is_archived:
        raise _bad_request(
            f"supervisor_id {supervisor_id} refers to an archived employee."
        )
    if not supervisor.employment_status.is_active_status:
        raise _bad_request(
            f"supervisor_id {supervisor_id} refers to an employee whose "
            f"current status ('{supervisor.employment_status.label}') is not active."
        )
    return supervisor


# ---------------------------------------------------------------------------
# Personal fields: SSN and date of birth
# ---------------------------------------------------------------------------

# Oldest plausible living person, used as a sanity floor for DOB.
_MAX_AGE_YEARS = 120


def normalize_ssn(raw: str | None) -> str | None:
    """Strip an SSN down to its digits.

    Accepts user input like "123-45-6789" or "123 45 6789" and returns the bare
    9-digit string. Returns None for empty/blank input so the field stays
    optional. This does NOT validate length — call validate_ssn_format for that.
    """
    if raw is None:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or None


def validate_ssn_format(ssn: str) -> None:
    """Require exactly 9 digits. Assumes the value has been normalized."""
    if len(ssn) != 9 or not ssn.isdigit():
        raise _bad_request(
            "Social Security Number must be exactly 9 digits (numbers only)."
        )


def validate_ssn_unique(
    db: Session,
    ssn: str,
    excluding_employee_id: int | None = None,
) -> None:
    """Reject a duplicate SSN.

    Matches against ALL employees — including archived/soft-deleted ones — so a
    person who was terminated can't be silently re-added under a new record. On
    an edit, pass excluding_employee_id so the employee doesn't collide with
    their own existing SSN.
    """
    query = db.query(Employee).filter(Employee.ssn == ssn)
    if excluding_employee_id is not None:
        query = query.filter(Employee.id != excluding_employee_id)
    existing = query.first()
    if existing is not None:
        label = (
            f"{existing.first_name} {existing.last_name} "
            f"({existing.employee_number})"
        )
        suffix = " — that record is archived" if existing.is_archived else ""
        raise _bad_request(
            f"An employee with this Social Security Number already exists: "
            f"{label}{suffix}."
        )


def validate_employee_number_unique(
    db: Session,
    employee_number: str,
    excluding_employee_id: int | None = None,
) -> None:
    """Reject a duplicate employee number.

    The `employee_number` column already carries a DB-level unique constraint,
    but that only surfaces as an IntegrityError after commit. This gives the
    create/update flows a friendly, case-insensitive pre-commit check.

    Matches against ALL employees — including archived/soft-deleted ones — so a
    number belonging to a terminated record can't be silently reused. On an edit,
    pass excluding_employee_id so the employee doesn't collide with their own
    existing number.
    """
    query = db.query(Employee).filter(
        Employee.employee_number_normalized() == employee_number.strip().lower()
    )
    if excluding_employee_id is not None:
        query = query.filter(Employee.id != excluding_employee_id)
    existing = query.first()
    if existing is not None:
        label = f"{existing.first_name} {existing.last_name}"
        suffix = " — that record is archived" if existing.is_archived else ""
        raise _bad_request(
            f"Employee number '{employee_number}' is already in use by "
            f"{label}{suffix}."
        )


def validate_dob(dob: date) -> None:
    """Date of birth can't be in the future or absurdly far in the past."""
    today = date.today()
    if dob > today:
        raise _bad_request("Date of birth cannot be in the future.")
    if dob.year < today.year - _MAX_AGE_YEARS:
        raise _bad_request(
            f"Date of birth is not valid (more than {_MAX_AGE_YEARS} years ago)."
        )
