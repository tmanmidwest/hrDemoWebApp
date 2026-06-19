"""Employee CRUD endpoints — the main API surface Saviynt consumes.

Key behaviors:
- Archived employees are HIDDEN by default. Use `?include_archived=true` to include
  them, or `?archived_only=true` to see only archived records.
- `?updated_since=<iso8601>` enables incremental sync for IGA integrations.
- `?eligible_supervisor=true` returns only employees who can be assigned as a
  supervisor (active status, not archived); pair with `?exclude_id=<id>` when
  editing to prevent self-supervision in the UI.
- The order of records is configurable: by default we sort active employees
  first (per the spec), then other statuses, then archived if included.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Employee, EmploymentStatus
from app.schemas.employee import EmployeeCreate, EmployeeOut, EmployeeUpdate
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, get_authenticated_principal
from app.services.employee_validation import (
    resolve_employment_status_by_value,
    validate_country_id,
    validate_department,
    validate_employment_status,
    validate_job_title_belongs_to_department,
    validate_location,
    validate_state_belongs_to_country,
    validate_supervisor,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/employees", tags=["employees"])


def _emp_label(employee: Employee) -> str:
    """Human label for an employee in audit events."""
    return f"{employee.first_name} {employee.last_name} ({employee.employee_number})"


# Whitelist of sortable columns to prevent SQL injection via the sort parameter.
SORTABLE_FIELDS: dict[str, object] = {
    "id": Employee.id,
    "employee_number": Employee.employee_number,
    "first_name": Employee.first_name,
    "last_name": Employee.last_name,
    "hire_date": Employee.hire_date,
    "termination_date": Employee.termination_date,
    "created_at": Employee.created_at,
    "updated_at": Employee.updated_at,
}


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[EmployeeOut])
def list_employees(
    # Pagination
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    # Archive filtering — Saviynt may want all three modes
    include_archived: bool = Query(
        default=False,
        description="Include archived employees in the result. Hidden by default.",
    ),
    archived_only: bool = Query(
        default=False,
        description="Return ONLY archived employees. Overrides include_archived.",
    ),
    # Status / dept filters
    employment_status_id: int | None = None,
    department_id: int | None = None,
    is_active_status: bool | None = Query(
        default=None,
        description=(
            "Filter by whether the assigned employment status is considered "
            "currently active. True = active employees, False = non-active."
        ),
    ),
    # Incremental sync
    updated_since: datetime | None = Query(
        default=None,
        description="ISO-8601 datetime. Returns only records updated at or after this time.",
    ),
    # Supervisor picker mode
    eligible_supervisor: bool = Query(
        default=False,
        description=(
            "Return only employees eligible to be assigned as a supervisor "
            "(active status, not archived). For dropdowns."
        ),
    ),
    exclude_id: int | None = Query(
        default=None,
        description="When using eligible_supervisor on an edit form, exclude this employee.",
    ),
    # Sort
    sort: str = Query(
        default="last_name",
        description=f"Sort field. One of: {', '.join(sorted(SORTABLE_FIELDS.keys()))}",
    ),
    order: Literal["asc", "desc"] = Query(default="asc"),
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> list[Employee]:
    if sort not in SORTABLE_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid sort field '{sort}'. Allowed: "
                + ", ".join(sorted(SORTABLE_FIELDS.keys()))
            ),
        )

    query = db.query(Employee)

    # Archive handling
    if archived_only:
        query = query.filter(Employee.is_archived.is_(True))
    elif not include_archived:
        query = query.filter(Employee.is_archived.is_(False))
    # else (include_archived=true) — no filter, return both

    if employment_status_id is not None:
        query = query.filter(Employee.employment_status_id == employment_status_id)
    if department_id is not None:
        query = query.filter(Employee.department_id == department_id)
    if updated_since is not None:
        query = query.filter(Employee.updated_at >= updated_since)
    if is_active_status is not None:
        query = query.join(Employee.employment_status).filter(
            EmploymentStatus.is_active_status == is_active_status
        )

    if eligible_supervisor:
        query = (
            query.filter(Employee.is_archived.is_(False))
            .join(Employee.employment_status, isouter=False)
            .filter(EmploymentStatus.is_active_status.is_(True))
        )
        if exclude_id is not None:
            query = query.filter(Employee.id != exclude_id)

    # Apply sort
    sort_col = SORTABLE_FIELDS[sort]
    order_fn = desc if order == "desc" else asc
    # Per spec: "Should always display Active employees first and inactive
    # employees last." Achieve this by sorting on is_active_status DESC first,
    # then by the user-requested column.
    if not eligible_supervisor:  # Skip secondary sort if already filtered to active
        query = query.join(Employee.employment_status, isouter=False).order_by(
            desc(EmploymentStatus.is_active_status),
            order_fn(sort_col),
        )
    else:
        query = query.order_by(order_fn(sort_col))

    return query.offset(offset).limit(limit).all()


# ---------------------------------------------------------------------------
# Get single
# ---------------------------------------------------------------------------


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found."
        )
    return employee


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(
    body: EmployeeCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    # Validate all FKs and cross-FK rules
    validate_country_id(db, body.country_id)
    if body.state_province_id is not None:
        validate_state_belongs_to_country(
            db, body.state_province_id, body.country_id
        )
    validate_employment_status(db, body.employment_status_id)
    validate_department(db, body.department_id)
    validate_job_title_belongs_to_department(
        db, body.job_title_id, body.department_id
    )
    if body.location_id is not None:
        validate_location(db, body.location_id)

    # supervisor_id is required unless the employees table is empty
    if body.supervisor_id is None:
        any_existing = db.query(Employee.id).first()
        if any_existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "supervisor_id is required (the only exception is the very "
                    "first employee created on an empty employees table)."
                ),
            )
    else:
        validate_supervisor(db, body.supervisor_id)

    employee = Employee(**body.model_dump())
    db.add(employee)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "employee_number" in msg or "unique" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Employee number '{body.employee_number}' already exists.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database integrity error: {exc.orig}",
        ) from None

    db.refresh(employee)
    log.info(
        "employee_created",
        extra={
            "employee_id": employee.id,
            "employee_number": employee.employee_number,
            "by": principal.identifier,
        },
    )
    record_event(
        category="employee",
        event_type="employee.created",
        **principal_actor(principal),
        target_type="employee",
        target_id=employee.id,
        target_label=_emp_label(employee),
        message=f"Created employee {_emp_label(employee)}",
        detail={"surface": "api", "employee_number": employee.employee_number},
    )
    return employee


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found."
        )

    data = body.model_dump(exclude_unset=True)

    # Resolve employment_status_value -> employment_status_id if provided.
    # Mutually exclusive with explicit employment_status_id.
    if "employment_status_value" in data:
        if "employment_status_id" in data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Provide either employment_status_id OR "
                    "employment_status_value, not both."
                ),
            )
        resolved = resolve_employment_status_by_value(
            db, data["employment_status_value"]
        )
        data["employment_status_id"] = resolved.id
    # Always drop the alias field — it isn't a column on the Employee model.
    data.pop("employment_status_value", None)

    # Determine effective values (incoming-or-existing) for cross-FK checks
    eff_country_id = data.get("country_id", employee.country_id)
    eff_state_id = data.get(
        "state_province_id",
        employee.state_province_id,
    )
    eff_dept_id = data.get("department_id", employee.department_id)
    eff_title_id = data.get("job_title_id", employee.job_title_id)
    eff_status_id = data.get("employment_status_id", employee.employment_status_id)
    eff_location_id = data.get("location_id", employee.location_id)
    eff_hire = data.get("hire_date", employee.hire_date)
    eff_term = data.get("termination_date", employee.termination_date)

    if "country_id" in data:
        validate_country_id(db, eff_country_id)
    # If either country or state changed, re-validate the pair
    if "country_id" in data or "state_province_id" in data:
        if eff_state_id is not None:
            validate_state_belongs_to_country(db, eff_state_id, eff_country_id)
    if "employment_status_id" in data:
        validate_employment_status(db, eff_status_id)
    if "department_id" in data:
        validate_department(db, eff_dept_id)
    if "department_id" in data or "job_title_id" in data:
        validate_job_title_belongs_to_department(db, eff_title_id, eff_dept_id)
    if "location_id" in data and eff_location_id is not None:
        validate_location(db, eff_location_id)
    if "supervisor_id" in data and data["supervisor_id"] is not None:
        validate_supervisor(db, data["supervisor_id"], excluding_employee_id=employee_id)
    if eff_term is not None and eff_term < eff_hire:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="termination_date must be on or after hire_date.",
        )

    for field, value in data.items():
        setattr(employee, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "employee_number" in msg or "unique" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That employee_number is already in use by another employee.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database integrity error: {exc.orig}",
        ) from None

    db.refresh(employee)
    log.info(
        "employee_updated",
        extra={
            "employee_id": employee.id,
            "fields": list(data.keys()),
            "by": principal.identifier,
        },
    )
    record_event(
        category="employee",
        event_type="employee.updated",
        **principal_actor(principal),
        target_type="employee",
        target_id=employee.id,
        target_label=_emp_label(employee),
        message=f"Updated employee {_emp_label(employee)}",
        detail={"surface": "api", "fields": list(data.keys())},
    )
    return employee


# ---------------------------------------------------------------------------
# Archive / Restore (soft delete)
# ---------------------------------------------------------------------------


@router.post("/{employee_id}/archive", response_model=EmployeeOut)
def archive_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    """Soft-delete an employee. The record stays in the database but is hidden
    from default list views and excluded from supervisor pickers.
    """
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found."
        )

    if not employee.is_archived:
        employee.is_archived = True
        employee.archived_at = datetime.now(UTC)
        db.commit()
        db.refresh(employee)
        log.info(
            "employee_archived",
            extra={"employee_id": employee_id, "by": principal.identifier},
        )
        record_event(
            category="employee",
            event_type="employee.archived",
            **principal_actor(principal),
            target_type="employee",
            target_id=employee.id,
            target_label=_emp_label(employee),
            message=f"Archived employee {_emp_label(employee)}",
            detail={"surface": "api"},
        )
    return employee


@router.post("/{employee_id}/restore", response_model=EmployeeOut)
def restore_employee(
    employee_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    """Restore an archived employee. Reverses /archive."""
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found."
        )

    if employee.is_archived:
        employee.is_archived = False
        employee.archived_at = None
        db.commit()
        db.refresh(employee)
        log.info(
            "employee_restored",
            extra={"employee_id": employee_id, "by": principal.identifier},
        )
        record_event(
            category="employee",
            event_type="employee.restored",
            **principal_actor(principal),
            target_type="employee",
            target_id=employee.id,
            target_label=_emp_label(employee),
            message=f"Restored employee {_emp_label(employee)}",
            detail={"surface": "api"},
        )
    return employee

# ---------------------------------------------------------------------------
# Terminate / Reactivate (IGA-friendly lifecycle operations)
# ---------------------------------------------------------------------------
#
# These are atomic, single-call versions of "change status + set termination
# date" that IGA platforms like Saviynt prefer to call. They avoid the
# coupled-write problem where a connector forgets to set termination_date
# alongside a Terminated status, or forgets to clear it on reactivation.
#
# They are NOT the same as /archive and /restore:
#   - archive/restore = soft-delete the row (hide from default lists)
#   - terminate/reactivate = employment lifecycle (still a real employee,
#     just no longer working here)
# An employee can be terminated without being archived (typical pattern: keep
# the record visible for reporting/access reviews for some retention window,
# then archive later).


# Default value codes — must match the seeded statuses in services/seed_data.py
DEFAULT_TERMINATED_VALUE = 3   # "Terminated"
DEFAULT_ACTIVE_VALUE = 1       # "Active"


class TerminateRequest(BaseModel):
    """Body for POST /employees/{id}/terminate."""

    termination_date: date | None = Field(
        default=None,
        description=(
            "Effective termination date. Defaults to today (UTC) if omitted. "
            "Must be on or after the employee's hire_date."
        ),
    )
    employment_status_value: int = Field(
        default=DEFAULT_TERMINATED_VALUE,
        description=(
            "Status value to set. Defaults to 3 (Terminated). Override if your "
            "org distinguishes e.g. voluntary vs involuntary termination via "
            "custom statuses."
        ),
    )


class ReactivateRequest(BaseModel):
    """Body for POST /employees/{id}/reactivate."""

    employment_status_value: int = Field(
        default=DEFAULT_ACTIVE_VALUE,
        description=(
            "Status value to set on reactivation. Defaults to 1 (Active)."
        ),
    )
    clear_termination_date: bool = Field(
        default=True,
        description=(
            "If true (default), clears termination_date. Set false if you want "
            "to keep the historical termination date for reporting."
        ),
    )


@router.post("/{employee_id}/terminate", response_model=EmployeeOut)
def terminate_employee(
    employee_id: int,
    body: TerminateRequest | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    """Mark an employee as terminated.

    Atomic version of "set employment_status to Terminated AND set
    termination_date" — intended for IGA platforms like Saviynt to call as a
    single operation.

    Idempotent: calling on an already-terminated employee returns 200 with the
    current state unchanged (except termination_date, which will update if a
    new one is supplied).

    Refuses on archived employees — archive and unarchive those separately if
    you really need to update them.
    """
    if body is None:
        body = TerminateRequest()

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found."
        )
    if employee.is_archived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot terminate an archived employee. Restore the record "
                "first via /restore if you need to update it."
            ),
        )

    target_status = resolve_employment_status_by_value(
        db, body.employment_status_value
    )
    effective_term_date = body.termination_date or datetime.now(UTC).date()
    if effective_term_date < employee.hire_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"termination_date ({effective_term_date}) must be on or after "
                f"hire_date ({employee.hire_date})."
            ),
        )

    employee.employment_status_id = target_status.id
    employee.termination_date = effective_term_date
    db.commit()
    db.refresh(employee)
    log.info(
        "employee_terminated",
        extra={
            "employee_id": employee_id,
            "status_value": body.employment_status_value,
            "termination_date": effective_term_date.isoformat(),
            "by": principal.identifier,
        },
    )
    record_event(
        category="employee",
        event_type="employee.terminated",
        **principal_actor(principal),
        target_type="employee",
        target_id=employee.id,
        target_label=_emp_label(employee),
        message=f"Terminated employee {_emp_label(employee)}",
        detail={
            "surface": "api",
            "status_value": body.employment_status_value,
            "termination_date": effective_term_date.isoformat(),
        },
    )
    return employee


@router.post("/{employee_id}/reactivate", response_model=EmployeeOut)
def reactivate_employee(
    employee_id: int,
    body: ReactivateRequest | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Employee:
    """Reverse a termination — set status back to Active and clear the
    termination date.

    Idempotent: calling on an already-active employee returns 200 with the
    current state.

    Refuses on archived employees for the same reason as /terminate.
    """
    if body is None:
        body = ReactivateRequest()

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found."
        )
    if employee.is_archived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot reactivate an archived employee. Restore the record "
                "first via /restore if you need to update it."
            ),
        )

    target_status = resolve_employment_status_by_value(
        db, body.employment_status_value
    )
    employee.employment_status_id = target_status.id
    if body.clear_termination_date:
        employee.termination_date = None
    db.commit()
    db.refresh(employee)
    log.info(
        "employee_reactivated",
        extra={
            "employee_id": employee_id,
            "status_value": body.employment_status_value,
            "by": principal.identifier,
        },
    )
    record_event(
        category="employee",
        event_type="employee.reactivated",
        **principal_actor(principal),
        target_type="employee",
        target_id=employee.id,
        target_label=_emp_label(employee),
        message=f"Reactivated employee {_emp_label(employee)}",
        detail={"surface": "api", "status_value": body.employment_status_value},
    )
    return employee
