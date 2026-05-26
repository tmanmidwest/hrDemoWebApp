"""HTML UI for managing employees."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import asc, desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AppUser,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    StateProvince,
)
from app.services.employee_validation import (
    validate_country_id,
    validate_department,
    validate_employment_status,
    validate_job_title_belongs_to_department,
    validate_state_belongs_to_country,
    validate_supervisor,
)
from app.ui.dependencies import require_ui_user
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/employees", tags=["ui"], include_in_schema=False)


OPTIONAL_COLUMNS = [
    {"key": "department", "label": "Department", "default": True},
    {"key": "job_title", "label": "Job Title", "default": True},
    {"key": "work_email", "label": "Work Email", "default": True},
    {"key": "supervisor", "label": "Supervisor", "default": True},
    {"key": "hire_date", "label": "Hire Date", "default": True},
    {"key": "country", "label": "Country", "default": False},
]


SORT_COLS = {
    "employee_number": Employee.employee_number,
    "last_name": Employee.last_name,
    "hire_date": Employee.hire_date,
}


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/")
def list_employees(
    request: Request,
    view: Literal["active", "all", "archived"] = "active",
    sort: str = "last_name",
    order: Literal["asc", "desc"] = "asc",
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    query = db.query(Employee).join(Employee.employment_status)

    if view == "active":
        query = query.filter(Employee.is_archived.is_(False))
    elif view == "archived":
        query = query.filter(Employee.is_archived.is_(True))
    # else "all" — no archive filter

    if sort not in SORT_COLS:
        sort = "last_name"
    order_fn = desc if order == "desc" else asc
    sort_col = SORT_COLS[sort]
    # Active-first per spec
    query = query.order_by(
        desc(EmploymentStatus.is_active_status),
        order_fn(sort_col),
    )

    employees = query.all()

    # Counts for header
    active_count = (
        db.query(func.count(Employee.id))
        .join(Employee.employment_status)
        .filter(Employee.is_archived.is_(False), EmploymentStatus.is_active_status.is_(True))
        .scalar()
        or 0
    )
    inactive_count = (
        db.query(func.count(Employee.id))
        .join(Employee.employment_status)
        .filter(Employee.is_archived.is_(False), EmploymentStatus.is_active_status.is_(False))
        .scalar()
        or 0
    )
    archived_count = (
        db.query(func.count(Employee.id)).filter(Employee.is_archived.is_(True)).scalar() or 0
    )

    return render(
        request,
        "employees/list.html",
        current_user=user,
        active_section="employees",
        employees=employees,
        view=view,
        sort=sort,
        order=order,
        counts={
            "active": active_count,
            "inactive": inactive_count,
            "archived": archived_count,
        },
        optional_cols=OPTIONAL_COLUMNS,
    )


# ---------------------------------------------------------------------------
# Add / Edit form helpers
# ---------------------------------------------------------------------------


def _form_dropdown_data(
    db: Session,
    selected_country_id: int | None,
    selected_department_id: int | None,
    exclude_employee_id: int | None,
) -> dict[str, object]:
    """Pre-load all the dropdown data the employee form needs."""
    countries = (
        db.query(Country).filter(Country.is_active.is_(True)).order_by(Country.name).all()
    )
    statuses = (
        db.query(EmploymentStatus).order_by(EmploymentStatus.value).all()
    )
    departments = (
        db.query(Department)
        .filter(Department.is_active.is_(True))
        .order_by(Department.name)
        .all()
    )

    states: list[StateProvince] = []
    if selected_country_id is not None:
        states = (
            db.query(StateProvince)
            .filter(
                StateProvince.country_id == selected_country_id,
                StateProvince.is_active.is_(True),
            )
            .order_by(StateProvince.name)
            .all()
        )

    job_titles: list[JobTitle] = []
    if selected_department_id is not None:
        job_titles = (
            db.query(JobTitle)
            .filter(
                JobTitle.department_id == selected_department_id,
                JobTitle.is_active.is_(True),
            )
            .order_by(JobTitle.name)
            .all()
        )

    # Eligible supervisors: active status, not archived, not this employee
    sup_query = (
        db.query(Employee)
        .join(Employee.employment_status)
        .filter(
            Employee.is_archived.is_(False),
            EmploymentStatus.is_active_status.is_(True),
        )
        .order_by(Employee.last_name, Employee.first_name)
    )
    if exclude_employee_id is not None:
        sup_query = sup_query.filter(Employee.id != exclude_employee_id)
    eligible_supervisors = sup_query.all()

    return {
        "countries": countries,
        "statuses": statuses,
        "departments": departments,
        "states": states,
        "job_titles": job_titles,
        "eligible_supervisors": eligible_supervisors,
    }


def _no_eligible_supervisors(db: Session) -> bool:
    """True if no employee is currently eligible to be a supervisor.

    An eligible supervisor is one who is not archived and whose current
    employment status is_active_status == True. We use this — rather than a
    simple "is the employees table empty?" check — so that the bootstrap case
    also covers DBs where employees exist but none are activatable yet (e.g.,
    only the seeded "Not Active" sample employees are present). Without this,
    the UI deadlocks: the supervisor field is required, but no row can be
    chosen, and the seeded rows can't be activated either because editing
    them also demands a supervisor.
    """
    return (
        db.query(Employee.id)
        .join(Employee.employment_status)
        .filter(
            Employee.is_archived.is_(False),
            EmploymentStatus.is_active_status.is_(True),
        )
        .first()
        is None
    )


# ---------------------------------------------------------------------------
# Show "new" form
# ---------------------------------------------------------------------------


@router.get("/new")
def show_new_form(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    dropdowns = _form_dropdown_data(db, None, None, None)
    return render(
        request,
        "employees/form.html",
        current_user=user,
        active_section="employees",
        employee=None,
        form={},  # empty form
        form_action="/ui/employees/new",
        must_have_supervisor=not _no_eligible_supervisors(db),
        **dropdowns,
    )


# ---------------------------------------------------------------------------
# Show "edit" form
# ---------------------------------------------------------------------------


@router.get("/{employee_id}/edit")
def show_edit_form(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")

    dropdowns = _form_dropdown_data(
        db,
        selected_country_id=employee.country_id,
        selected_department_id=employee.department_id,
        exclude_employee_id=employee.id,
    )

    form_data = {
        "employee_number": employee.employee_number,
        "first_name": employee.first_name,
        "middle_name": employee.middle_name,
        "last_name": employee.last_name,
        "address_line_1": employee.address_line_1,
        "address_line_2": employee.address_line_2,
        "city": employee.city,
        "country_id": employee.country_id,
        "state_province_id": employee.state_province_id,
        "postal_code": employee.postal_code,
        "home_phone": employee.home_phone,
        "personal_email": employee.personal_email,
        "work_email": employee.work_email,
        "cost_center": employee.cost_center,
        "employment_status_id": employee.employment_status_id,
        "department_id": employee.department_id,
        "job_title_id": employee.job_title_id,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "termination_date": (
            employee.termination_date.isoformat() if employee.termination_date else None
        ),
        "supervisor_id": employee.supervisor_id,
    }

    return render(
        request,
        "employees/form.html",
        current_user=user,
        active_section="employees",
        employee=employee,
        form=form_data,
        form_action=f"/ui/employees/{employee.id}/edit",
        must_have_supervisor=employee.supervisor_id is not None,  # Bootstrap rows (e.g., the first employee) legitimately have no supervisor; preserve that.
        **dropdowns,
    )


# ---------------------------------------------------------------------------
# HTMX partials — dependent dropdowns
# ---------------------------------------------------------------------------


@router.get("/_states-options")
def state_options(
    country_id: int | None = None,
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]
    _user: AppUser = Depends(require_ui_user),
) -> Response:
    states: list[StateProvince] = []
    if country_id is not None:
        states = (
            db.query(StateProvince)
            .filter(
                StateProvince.country_id == country_id,
                StateProvince.is_active.is_(True),
            )
            .order_by(StateProvince.name)
            .all()
        )
    return render(request, "employees/_state_options.html", states=states)


@router.get("/_job-title-options")
def job_title_options(
    department_id: int | None = None,
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]
    _user: AppUser = Depends(require_ui_user),
) -> Response:
    titles: list[JobTitle] = []
    if department_id is not None:
        titles = (
            db.query(JobTitle)
            .filter(
                JobTitle.department_id == department_id,
                JobTitle.is_active.is_(True),
            )
            .order_by(JobTitle.name)
            .all()
        )
    return render(request, "employees/_job_title_options.html", job_titles=titles)


# ---------------------------------------------------------------------------
# Form parsing helpers
# ---------------------------------------------------------------------------


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_str(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


# ---------------------------------------------------------------------------
# Form submission helper
# ---------------------------------------------------------------------------


async def _parse_employee_form(request: Request) -> dict[str, object]:
    """Read the form payload into a dict suitable for setattr-ing onto an Employee."""
    form = await request.form()
    return {
        "employee_number": _parse_str(form.get("employee_number")),  # type: ignore[arg-type]
        "first_name": _parse_str(form.get("first_name")),  # type: ignore[arg-type]
        "middle_name": _parse_str(form.get("middle_name")),  # type: ignore[arg-type]
        "last_name": _parse_str(form.get("last_name")),  # type: ignore[arg-type]
        "address_line_1": _parse_str(form.get("address_line_1")),  # type: ignore[arg-type]
        "address_line_2": _parse_str(form.get("address_line_2")),  # type: ignore[arg-type]
        "city": _parse_str(form.get("city")),  # type: ignore[arg-type]
        "country_id": _parse_int(form.get("country_id")),  # type: ignore[arg-type]
        "state_province_id": _parse_int(form.get("state_province_id")),  # type: ignore[arg-type]
        "postal_code": _parse_str(form.get("postal_code")),  # type: ignore[arg-type]
        "home_phone": _parse_str(form.get("home_phone")),  # type: ignore[arg-type]
        "personal_email": _parse_str(form.get("personal_email")),  # type: ignore[arg-type]
        "work_email": _parse_str(form.get("work_email")),  # type: ignore[arg-type]
        "cost_center": _parse_str(form.get("cost_center")),  # type: ignore[arg-type]
        "employment_status_id": _parse_int(form.get("employment_status_id")),  # type: ignore[arg-type]
        "department_id": _parse_int(form.get("department_id")),  # type: ignore[arg-type]
        "job_title_id": _parse_int(form.get("job_title_id")),  # type: ignore[arg-type]
        "hire_date": _parse_date(form.get("hire_date")),  # type: ignore[arg-type]
        "termination_date": _parse_date(form.get("termination_date")),  # type: ignore[arg-type]
        "supervisor_id": _parse_int(form.get("supervisor_id")),  # type: ignore[arg-type]
    }


def _render_form_with_error(
    request: Request,
    user: AppUser,
    db: Session,
    employee: Employee | None,
    form_data: dict[str, object],
    error_msg: str,
    must_have_supervisor: bool,
) -> Response:
    dropdowns = _form_dropdown_data(
        db,
        selected_country_id=form_data.get("country_id"),  # type: ignore[arg-type]
        selected_department_id=form_data.get("department_id"),  # type: ignore[arg-type]
        exclude_employee_id=employee.id if employee else None,
    )
    # Stringify dates back for the form
    display_form = dict(form_data)
    for k in ("hire_date", "termination_date"):
        v = display_form.get(k)
        if isinstance(v, date):
            display_form[k] = v.isoformat()
    return render(
        request,
        "employees/form.html",
        current_user=user,
        active_section="employees",
        employee=employee,
        form=display_form,
        form_action=(
            f"/ui/employees/{employee.id}/edit" if employee else "/ui/employees/new"
        ),
        error=error_msg,
        must_have_supervisor=must_have_supervisor,
        **dropdowns,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/new")
async def create_employee(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    data = await _parse_employee_form(request)
    must_have_supervisor = not _no_eligible_supervisors(db)

    try:
        if data["country_id"] is None:
            raise ValueError("Country is required.")
        validate_country_id(db, data["country_id"])  # type: ignore[arg-type]
        if data["state_province_id"] is not None:
            validate_state_belongs_to_country(
                db, data["state_province_id"], data["country_id"]  # type: ignore[arg-type]
            )
        if data["employment_status_id"] is None:
            raise ValueError("Employment status is required.")
        validate_employment_status(db, data["employment_status_id"])  # type: ignore[arg-type]
        if data["department_id"] is None:
            raise ValueError("Department is required.")
        validate_department(db, data["department_id"])  # type: ignore[arg-type]
        if data["job_title_id"] is None:
            raise ValueError("Job title is required.")
        validate_job_title_belongs_to_department(
            db, data["job_title_id"], data["department_id"]  # type: ignore[arg-type]
        )
        if data["hire_date"] is None:
            raise ValueError("Hire date is required.")
        if data["termination_date"] is not None and data["termination_date"] < data["hire_date"]:  # type: ignore[operator]
            raise ValueError("Termination date must be on or after hire date.")
        if must_have_supervisor and data["supervisor_id"] is None:
            raise ValueError("Supervisor is required.")
        if data["supervisor_id"] is not None:
            validate_supervisor(db, data["supervisor_id"])  # type: ignore[arg-type]
        for field in ("employee_number", "first_name", "last_name"):
            if not data[field]:
                raise ValueError(f"{field.replace('_', ' ').title()} is required.")
    except (HTTPException, ValueError) as exc:
        msg = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _render_form_with_error(
            request, user, db, None, data, msg, must_have_supervisor
        )

    employee = Employee(**data)
    db.add(employee)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "employee_number" in msg or "unique" in msg:
            return _render_form_with_error(
                request,
                user,
                db,
                None,
                data,
                f"Employee number '{data['employee_number']}' already exists.",
                must_have_supervisor,
            )
        return _render_form_with_error(
            request, user, db, None, data, f"Database error: {exc.orig}", must_have_supervisor
        )

    log.info(
        "ui_employee_created",
        extra={"employee_id": employee.id, "by": user.username},
    )
    flash(request, f"Employee {employee.employee_number} created.", "success")
    return RedirectResponse(url="/ui/employees", status_code=303)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.post("/{employee_id}/edit")
async def update_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")

    data = await _parse_employee_form(request)

    # An employee that legitimately has no supervisor today (e.g., the bootstrap
    # first employee) can keep saving with no supervisor. Once they have one,
    # they must keep one.
    must_have_supervisor = employee.supervisor_id is not None

    try:
        if data["country_id"] is None:
            raise ValueError("Country is required.")
        validate_country_id(db, data["country_id"])  # type: ignore[arg-type]
        if data["state_province_id"] is not None:
            validate_state_belongs_to_country(
                db, data["state_province_id"], data["country_id"]  # type: ignore[arg-type]
            )
        validate_employment_status(db, data["employment_status_id"])  # type: ignore[arg-type]
        validate_department(db, data["department_id"])  # type: ignore[arg-type]
        validate_job_title_belongs_to_department(
            db, data["job_title_id"], data["department_id"]  # type: ignore[arg-type]
        )
        if data["termination_date"] is not None and data["termination_date"] < data["hire_date"]:  # type: ignore[operator]
            raise ValueError("Termination date must be on or after hire date.")
        if must_have_supervisor and data["supervisor_id"] is None:
            raise ValueError("Supervisor is required.")
        if data["supervisor_id"] is not None:
            validate_supervisor(
                db, data["supervisor_id"], excluding_employee_id=employee_id  # type: ignore[arg-type]
            )
    except (HTTPException, ValueError) as exc:
        msg = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _render_form_with_error(request, user, db, employee, data, msg, must_have_supervisor)

    for field, value in data.items():
        setattr(employee, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "employee_number" in msg or "unique" in msg:
            return _render_form_with_error(
                request,
                user,
                db,
                employee,
                data,
                "That employee number is in use by another employee.",
                must_have_supervisor,
            )
        return _render_form_with_error(
            request, user, db, employee, data, f"Database error: {exc.orig}", must_have_supervisor
        )

    log.info("ui_employee_updated", extra={"employee_id": employee.id, "by": user.username})
    flash(request, f"Employee {employee.employee_number} updated.", "success")
    return RedirectResponse(url="/ui/employees", status_code=303)


# ---------------------------------------------------------------------------
# Archive / Restore
# ---------------------------------------------------------------------------


@router.post("/{employee_id}/archive")
def archive_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    if not employee.is_archived:
        employee.is_archived = True
        employee.archived_at = datetime.now(UTC)
        db.commit()
        log.info(
            "ui_employee_archived",
            extra={"employee_id": employee_id, "by": user.username},
        )
        flash(request, f"Employee {employee.employee_number} archived.", "success")
    return RedirectResponse(url="/ui/employees", status_code=303)


@router.post("/{employee_id}/restore")
def restore_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    if employee.is_archived:
        employee.is_archived = False
        employee.archived_at = None
        db.commit()
        log.info(
            "ui_employee_restored",
            extra={"employee_id": employee_id, "by": user.username},
        )
        flash(request, f"Employee {employee.employee_number} restored.", "success")
    return RedirectResponse(url="/ui/employees?view=archived", status_code=303)
