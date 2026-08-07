"""HTML UI for managing employees."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.db import get_db
from app.models import (
    AppUser,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    Location,
    StateProvince,
)
from app.services.employee_validation import (
    normalize_ssn,
    validate_country_id,
    validate_department,
    validate_dob,
    validate_employee_number_unique,
    validate_employment_status,
    validate_job_title,
    validate_job_title_belongs_to_department,
    validate_location,
    validate_ssn_format,
    validate_ssn_unique,
    validate_state_belongs_to_country,
    validate_supervisor,
)
from app.services import reference_managers
from app.services.audit import record_event
from app.ui.dependencies import (
    require_admin,
    require_employee_manager,
    require_ui_user,
)
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)


def _emp_label(employee: Employee) -> str:
    """Human label for an employee in audit events."""
    return f"{employee.first_name} {employee.last_name} ({employee.employee_number})"

router = APIRouter(prefix="/ui/employees", tags=["ui"], include_in_schema=False)


OPTIONAL_COLUMNS = [
    {"key": "department", "label": "Department", "default": True},
    {"key": "job_title", "label": "Job Title", "default": True},
    {"key": "work_email", "label": "Work Email", "default": True},
    {"key": "supervisor", "label": "Supervisor", "default": True},
    {"key": "hire_date", "label": "Hire Date", "default": True},
    {"key": "country", "label": "Country", "default": False},
    {"key": "location", "label": "Location", "default": False},
]


# Every visible column is sortable. The values here are only the *scalar*
# expressions that live directly on Employee; the related-table sorts
# (department, job_title, supervisor, country, location, status) are resolved
# inside list_employees because some need an explicit/aliased join.
SORT_KEYS = {
    "employee_number",
    "last_name",
    "status",
    "department",
    "job_title",
    "work_email",
    "supervisor",
    "hire_date",
    "country",
    "location",
}


def _parse_int(value: str | None) -> int | None:
    """Coerce a form value to an int, treating blank/non-numeric as None.

    Filter params arrive as strings (an unselected dropdown submits ""), so we
    can't type them as `int | None` on the endpoint without tripping FastAPI's
    422 on the empty string. Parse defensively instead.
    """
    if value is None:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def _parse_date(value: str | None) -> date | None:
    """Coerce an ISO date string (yyyy-mm-dd) to a date, or None if unparseable."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


# Maps the internal `filters` dict keys to their URL query-param names, so the
# active filters can be re-encoded into sort-header and view-tab links.
_FILTER_PARAM_NAMES = {
    "q": "q",
    "employee_number": "f_employee_number",
    "name": "f_name",
    "status": "f_status",
    "department": "f_department",
    "department_mode": "f_department_mode",
    "job_title": "f_job_title",
    "work_email": "f_work_email",
    "supervisor": "f_supervisor",
    "country": "f_country",
    "location": "f_location",
    "hire_from": "f_hire_from",
    "hire_to": "f_hire_to",
}


def _filter_params(filters: dict) -> dict:
    """Non-empty active filters keyed by their URL param name (for link building)."""
    out: dict[str, object] = {}
    for key, param in _FILTER_PARAM_NAMES.items():
        value = filters.get(key)
        if value not in (None, ""):
            out[param] = value
    return out


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/")
def list_employees(
    request: Request,
    view: Literal["active", "all", "archived"] = "active",
    sort: str = "last_name",
    order: Literal["asc", "desc"] = "asc",
    # Global search across identity/contact text fields.
    q: str = "",
    # Per-column filters. FK columns arrive as id strings ("" when unset);
    # text columns are substring matches; hire date is a range.
    f_employee_number: str = "",
    f_name: str = "",
    f_status: str = "",
    f_department: str = "",
    # "is" (default) matches the chosen department; "is_not" excludes it (while
    # still showing employees with no department).
    f_department_mode: str = "is",
    f_job_title: str = "",
    f_work_email: str = "",
    f_supervisor: str = "",
    f_country: str = "",
    f_location: str = "",
    f_hire_from: str = "",
    f_hire_to: str = "",
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    # Supervisor is self-referential, so sorting by supervisor name needs an
    # aliased join distinct from the eager-loaded `supervisor` relationship.
    supervisor_alias = aliased(Employee)

    # Outer joins for status/department/job title — they're optional now, and an
    # inner join would silently drop employees that don't have one yet.
    query = (
        db.query(Employee)
        .outerjoin(Employee.employment_status)
        .outerjoin(Employee.department)
        .outerjoin(Employee.job_title)
        .join(Employee.country)
        .outerjoin(Location, Employee.location_id == Location.id)
        .outerjoin(supervisor_alias, Employee.supervisor_id == supervisor_alias.id)
    )

    if view == "active":
        query = query.filter(Employee.is_archived.is_(False))
    elif view == "archived":
        query = query.filter(Employee.is_archived.is_(True))
    # else "all" — no archive filter

    # ---- Filters -----------------------------------------------------------
    q = q.strip()
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Employee.employee_number.ilike(like),
                Employee.first_name.ilike(like),
                Employee.last_name.ilike(like),
                Employee.work_email.ilike(like),
                Employee.personal_email.ilike(like),
            )
        )
    if f_employee_number.strip():
        query = query.filter(Employee.employee_number.ilike(f"%{f_employee_number.strip()}%"))
    if f_name.strip():
        name_like = f"%{f_name.strip()}%"
        query = query.filter(
            or_(Employee.first_name.ilike(name_like), Employee.last_name.ilike(name_like))
        )
    if f_work_email.strip():
        query = query.filter(Employee.work_email.ilike(f"%{f_work_email.strip()}%"))

    status_id = _parse_int(f_status)
    department_id = _parse_int(f_department)
    job_title_id = _parse_int(f_job_title)
    supervisor_id = _parse_int(f_supervisor)
    country_id = _parse_int(f_country)
    location_id = _parse_int(f_location)
    if status_id is not None:
        query = query.filter(Employee.employment_status_id == status_id)
    if department_id is not None:
        if f_department_mode == "is_not":
            # Exclude the department, but keep employees with no department.
            query = query.filter(
                or_(
                    Employee.department_id != department_id,
                    Employee.department_id.is_(None),
                )
            )
        else:
            query = query.filter(Employee.department_id == department_id)
    if job_title_id is not None:
        query = query.filter(Employee.job_title_id == job_title_id)
    if supervisor_id is not None:
        query = query.filter(Employee.supervisor_id == supervisor_id)
    if country_id is not None:
        query = query.filter(Employee.country_id == country_id)
    if location_id is not None:
        query = query.filter(Employee.location_id == location_id)

    hire_from = _parse_date(f_hire_from)
    hire_to = _parse_date(f_hire_to)
    if hire_from is not None:
        query = query.filter(Employee.hire_date >= hire_from)
    if hire_to is not None:
        query = query.filter(Employee.hire_date <= hire_to)

    # ---- Sorting -----------------------------------------------------------
    if sort not in SORT_KEYS:
        sort = "last_name"
    sort_exprs = {
        "employee_number": Employee.employee_number,
        "last_name": Employee.last_name,
        "status": EmploymentStatus.label,
        "department": Department.name,
        "job_title": JobTitle.name,
        "work_email": Employee.work_email,
        "supervisor": supervisor_alias.last_name,
        "hire_date": Employee.hire_date,
        "country": Country.name,
        "location": Location.name,
    }
    order_fn = desc if order == "desc" else asc
    # Active-first grouping is preserved, then the chosen column, then a stable
    # name tiebreaker so equal values don't reshuffle between requests.
    query = query.order_by(
        desc(EmploymentStatus.is_active_status),
        order_fn(sort_exprs[sort]),
        Employee.last_name,
        Employee.first_name,
    )

    employees = query.all()

    # Counts for header — these describe the whole dataset, not the filtered
    # view, so the summary line stays stable as filters change.
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

    # ---- Filter option lists (dropdowns), as {id, label} -------------------
    statuses = db.query(EmploymentStatus).order_by(EmploymentStatus.value).all()
    departments = db.query(Department).order_by(Department.name).all()
    job_titles = db.query(JobTitle).order_by(JobTitle.name).all()
    countries = db.query(Country).order_by(Country.name).all()
    locations = db.query(Location).order_by(Location.name).all()
    # Only employees who actually supervise someone are useful as a filter.
    supervisor_id_subq = (
        db.query(Employee.supervisor_id)
        .filter(Employee.supervisor_id.isnot(None))
        .distinct()
    )
    supervisors = (
        db.query(Employee)
        .filter(Employee.id.in_(supervisor_id_subq))
        .order_by(Employee.last_name, Employee.first_name)
        .all()
    )

    def _opts(rows, label) -> list[dict]:
        return [{"id": r.id, "label": label(r)} for r in rows]

    # Current filter values, echoed back to prefill the controls.
    filters = {
        "q": q,
        "employee_number": f_employee_number.strip(),
        "name": f_name.strip(),
        "status": status_id,
        "department": department_id,
        "job_title": job_title_id,
        "work_email": f_work_email.strip(),
        "supervisor": supervisor_id,
        "country": country_id,
        "location": location_id,
        "hire_from": f_hire_from.strip(),
        "hire_to": f_hire_to.strip(),
    }
    # Only record the exclude mode when it's actually excluding a department, so
    # it counts as an active filter (and persists in links) only when meaningful.
    if department_id is not None and f_department_mode == "is_not":
        filters["department_mode"] = "is_not"
    has_filters = any(filters.values())

    # Pre-encoded query strings so the sort headers and view tabs preserve the
    # active filters. Sort links append their own sort/order; tabs append view.
    active_params = {k: v for k, v in _filter_params(filters).items()}
    sort_qs = urlencode({"view": view, **active_params})
    tab_qs = urlencode({"sort": sort, "order": order, **active_params})

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
        filters=filters,
        has_filters=has_filters,
        filter_options={
            "statuses": _opts(statuses, lambda r: r.label),
            "departments": _opts(departments, lambda r: r.name),
            "job_titles": _opts(job_titles, lambda r: r.name),
            "supervisors": _opts(
                supervisors, lambda r: f"{r.first_name} {r.last_name} ({r.employee_number})"
            ),
            "countries": _opts(countries, lambda r: r.name),
            "locations": _opts(locations, lambda r: r.name),
        },
        sort_qs=sort_qs,
        tab_qs=tab_qs,
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

    locations = (
        db.query(Location)
        .filter(Location.is_active.is_(True))
        .order_by(Location.name)
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
        "locations": locations,
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
    user: AppUser = Depends(require_employee_manager),
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
        must_have_supervisor=False,
        reference_managers_enabled=reference_managers.is_enabled(db),
        custom_field_inputs=_custom_field_inputs(db),
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
    user: AppUser = Depends(require_employee_manager),
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
        "date_of_birth": (
            employee.date_of_birth.isoformat() if employee.date_of_birth else None
        ),
        # SSN is intentionally NOT prefilled — the input stays blank and the
        # template shows only the masked current value.
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
        "location_id": employee.location_id,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "termination_date": (
            employee.termination_date.isoformat() if employee.termination_date else None
        ),
        "supervisor_id": employee.supervisor_id,
        "is_reference_manager": employee.is_reference_manager,
    }

    return render(
        request,
        "employees/form.html",
        current_user=user,
        active_section="employees",
        employee=employee,
        form=form_data,
        form_action=f"/ui/employees/{employee.id}/edit",
        must_have_supervisor=False,  # Supervisor is optional.
        reference_managers_enabled=reference_managers.is_enabled(db),
        custom_field_inputs=_custom_field_inputs(db, employee=employee),
        **dropdowns,
    )


def _custom_fields_view(db: Session, employee: Employee) -> list[dict[str, str]]:
    """Read-only [{label, value}] of an employee's custom attributes.

    Ordered by the registry; only defined, non-empty values are shown. Used on the
    read-only detail page.
    """
    from app.services import custom_fields as cf

    values = employee.custom_fields or {}
    out: list[dict[str, str]] = []
    for definition in cf.list_definitions(db, active_only=True):
        if definition.key in values:
            out.append(
                {
                    "label": definition.label,
                    "value": cf.display_value(definition.data_type, values[definition.key]),
                }
            )
    return out


def _custom_field_inputs(
    db: Session,
    *,
    employee: Employee | None = None,
    submitted: object | None = None,
) -> list[dict[str, str]]:
    """Editable custom-field descriptors for the create/edit form.

    Each item is {key, label, data_type, description, value}. ``value`` is a
    string suitable for the control: booleans render as ""/"true"/"false".
    Source of the value: the re-submitted form (on validation error), else the
    existing employee (edit), else blank (new).
    """
    from app.services import custom_fields as cf

    stored = (employee.custom_fields or {}) if employee is not None else {}
    inputs: list[dict[str, str]] = []
    for d in cf.list_definitions(db, active_only=True):
        if submitted is not None:
            value = str(submitted.get(f"cf_{d.key}") or "")  # type: ignore[union-attr]
        else:
            v = stored.get(d.key)
            if d.data_type == "boolean":
                value = "true" if v is True else ("false" if v is False else "")
            else:
                value = "" if v is None else str(v)
        inputs.append(
            {
                "key": d.key,
                "label": d.label,
                "data_type": d.data_type,
                "description": d.description or "",
                "value": value,
            }
        )
    return inputs


def _apply_custom_fields(
    db: Session, form: object, employee: Employee | None
) -> tuple[dict[str, object], list[str]]:
    """Merge submitted ``cf_<key>`` inputs into a custom_fields bag.

    Starts from the employee's existing bag (edit) or empty (create). For each
    active definition present in the form, a blank clears the value and a
    non-blank is coerced to the field's type. Values for inactive definitions are
    left untouched. Returns (merged_bag, errors).
    """
    from app.services import custom_fields as cf

    merged: dict[str, object] = dict(
        employee.custom_fields or {}
    ) if employee is not None else {}
    errors: list[str] = []
    for d in cf.list_definitions(db, active_only=True):
        fname = f"cf_{d.key}"
        if fname not in form:  # type: ignore[operator]
            continue
        raw = str(form.get(fname) or "").strip()  # type: ignore[union-attr]
        if raw == "":
            merged.pop(d.key, None)
        else:
            try:
                merged[d.key] = cf.coerce_value(d.data_type, raw)
            except cf.CustomFieldError as exc:
                errors.append(f"{d.label}: {exc}")
    return merged, errors


# ---------------------------------------------------------------------------
# HTMX partials — dependent dropdowns
# ---------------------------------------------------------------------------


@router.get("/_states-options")
def state_options(
    country_id: int | None = None,
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]
    _user: AppUser = Depends(require_employee_manager),
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
    _user: AppUser = Depends(require_employee_manager),
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
# Read-only detail view
# ---------------------------------------------------------------------------
# NOTE: declared AFTER the static GET routes (/new, /_states-options,
# /_job-title-options) so this single-segment dynamic path doesn't shadow them.


@router.get("/{employee_id}")
def show_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    """Read-only employee detail page. Managers/admins only; has an Edit button."""
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    return render(
        request,
        "employees/detail.html",
        current_user=user,
        active_section="employees",
        employee=employee,
        custom_fields_view=_custom_fields_view(db, employee),
        profile=_profile_extras(employee),
    )


def _profile_extras(employee: Employee) -> dict[str, object]:
    """Presentational bits for the profile header: initials, avatar hue, tenure."""
    first = (employee.first_name or "").strip()
    last = (employee.last_name or "").strip()
    initials = (first[:1] + last[:1]).upper() or (employee.employee_number or "?")[:2].upper()
    name = f"{first} {last}".strip()
    # Stable per-person hue (0–359) so each avatar/banner has its own tint.
    hue = (sum(ord(c) for c in name) % 360) if name else 210

    tenure: str | None = None
    if employee.hire_date is not None:
        end = employee.termination_date or date.today()
        months = (end.year - employee.hire_date.year) * 12 + (
            end.month - employee.hire_date.month
        )
        if end.day < employee.hire_date.day:
            months -= 1
        months = max(months, 0)
        years, rem = divmod(months, 12)
        if years and rem:
            tenure = f"{years} yr {rem} mo"
        elif years:
            tenure = f"{years} yr"
        else:
            tenure = f"{rem} mo"

    return {"initials": initials, "hue": hue, "tenure": tenure}


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
        "date_of_birth": _parse_date(form.get("date_of_birth")),  # type: ignore[arg-type]
        "ssn": normalize_ssn(form.get("ssn")),  # type: ignore[arg-type]
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
        "location_id": _parse_int(form.get("location_id")),  # type: ignore[arg-type]
        "hire_date": _parse_date(form.get("hire_date")),  # type: ignore[arg-type]
        "termination_date": _parse_date(form.get("termination_date")),  # type: ignore[arg-type]
        "supervisor_id": _parse_int(form.get("supervisor_id")),  # type: ignore[arg-type]
        # Checkbox — present only when the reference-manager feature is enabled.
        "is_reference_manager": form.get("is_reference_manager") is not None,
    }


def _render_form_with_error(
    request: Request,
    user: AppUser,
    db: Session,
    employee: Employee | None,
    form_data: dict[str, object],
    error_msg: str,
    must_have_supervisor: bool,
    custom_field_inputs: list[dict[str, str]] | None = None,
) -> Response:
    dropdowns = _form_dropdown_data(
        db,
        selected_country_id=form_data.get("country_id"),  # type: ignore[arg-type]
        selected_department_id=form_data.get("department_id"),  # type: ignore[arg-type]
        exclude_employee_id=employee.id if employee else None,
    )
    # Stringify dates back for the form
    display_form = dict(form_data)
    for k in ("hire_date", "termination_date", "date_of_birth"):
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
        reference_managers_enabled=reference_managers.is_enabled(db),
        custom_field_inputs=custom_field_inputs
        if custom_field_inputs is not None
        else _custom_field_inputs(db, employee=employee),
        **dropdowns,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/new")
async def create_employee(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    data = await _parse_employee_form(request)
    form = await request.form()  # cached; used for cf_<key> custom-field inputs
    # Supervisor, department, job title, status, and hire date are all optional.
    must_have_supervisor = False

    def _error(msg: str) -> Response:
        return _render_form_with_error(
            request, user, db, None, data, msg, must_have_supervisor,
            custom_field_inputs=_custom_field_inputs(db, submitted=form),
        )

    try:
        if data["country_id"] is None:
            raise ValueError("Country is required.")
        validate_country_id(db, data["country_id"])  # type: ignore[arg-type]
        if data["state_province_id"] is not None:
            validate_state_belongs_to_country(
                db, data["state_province_id"], data["country_id"]  # type: ignore[arg-type]
            )
        if data["employment_status_id"] is not None:
            validate_employment_status(db, data["employment_status_id"])  # type: ignore[arg-type]
        if data["department_id"] is not None:
            validate_department(db, data["department_id"])  # type: ignore[arg-type]
        if data["job_title_id"] is not None:
            if data["department_id"] is not None:
                validate_job_title_belongs_to_department(
                    db, data["job_title_id"], data["department_id"]  # type: ignore[arg-type]
                )
            else:
                validate_job_title(db, data["job_title_id"])  # type: ignore[arg-type]
        if data["location_id"] is not None:
            validate_location(db, data["location_id"])  # type: ignore[arg-type]
        if (
            data["termination_date"] is not None
            and data["hire_date"] is not None
            and data["termination_date"] < data["hire_date"]  # type: ignore[operator]
        ):
            raise ValueError("Termination date must be on or after hire date.")
        if data["supervisor_id"] is not None:
            validate_supervisor(db, data["supervisor_id"])  # type: ignore[arg-type]
        if data["date_of_birth"] is not None:
            validate_dob(data["date_of_birth"])  # type: ignore[arg-type]
        if data["ssn"] is not None:
            validate_ssn_format(data["ssn"])  # type: ignore[arg-type]
            validate_ssn_unique(db, data["ssn"])  # type: ignore[arg-type]
        for field in ("employee_number", "first_name", "last_name"):
            if not data[field]:
                raise ValueError(f"{field.replace('_', ' ').title()} is required.")
        validate_employee_number_unique(db, data["employee_number"])  # type: ignore[arg-type]
    except (HTTPException, ValueError) as exc:
        msg = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _error(msg)

    # Custom attributes (cf_<key> inputs), coerced/validated against the registry.
    custom_values, cf_errors = _apply_custom_fields(db, form, None)
    if cf_errors:
        return _error("; ".join(cf_errors))
    data["custom_fields"] = custom_values

    # Marking a record as a static reference manager is only honored when the
    # feature is enabled for this instance; otherwise it stays a normal employee.
    if not reference_managers.is_enabled(db):
        data["is_reference_manager"] = False

    employee = Employee(**data)
    db.add(employee)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "ssn" in msg:
            return _error("An employee with this Social Security Number already exists.")
        if "employee_number" in msg or "unique" in msg:
            return _error(f"Employee number '{data['employee_number']}' already exists.")
        return _error(f"Database error: {exc.orig}")

    log.info(
        "ui_employee_created",
        extra={"employee_id": employee.id, "by": user.username},
    )
    record_event(
        category="employee",
        event_type="employee.created",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        target_id=employee.id,
        target_label=_emp_label(employee),
        message=f"Created employee {_emp_label(employee)}",
        detail={"surface": "ui", "employee_number": employee.employee_number},
        request=request,
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
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")

    data = await _parse_employee_form(request)
    form = await request.form()  # cached; used for cf_<key> custom-field inputs

    # Supervisor, department, job title, status, and hire date are all optional.
    must_have_supervisor = False

    def _error(msg: str) -> Response:
        return _render_form_with_error(
            request, user, db, employee, data, msg, must_have_supervisor,
            custom_field_inputs=_custom_field_inputs(db, submitted=form),
        )

    try:
        if data["country_id"] is None:
            raise ValueError("Country is required.")
        validate_country_id(db, data["country_id"])  # type: ignore[arg-type]
        if data["state_province_id"] is not None:
            validate_state_belongs_to_country(
                db, data["state_province_id"], data["country_id"]  # type: ignore[arg-type]
            )
        if data["employment_status_id"] is not None:
            validate_employment_status(db, data["employment_status_id"])  # type: ignore[arg-type]
        if data["department_id"] is not None:
            validate_department(db, data["department_id"])  # type: ignore[arg-type]
        if data["job_title_id"] is not None:
            if data["department_id"] is not None:
                validate_job_title_belongs_to_department(
                    db, data["job_title_id"], data["department_id"]  # type: ignore[arg-type]
                )
            else:
                validate_job_title(db, data["job_title_id"])  # type: ignore[arg-type]
        if data["location_id"] is not None:
            validate_location(db, data["location_id"])  # type: ignore[arg-type]
        if (
            data["termination_date"] is not None
            and data["hire_date"] is not None
            and data["termination_date"] < data["hire_date"]  # type: ignore[operator]
        ):
            raise ValueError("Termination date must be on or after hire date.")
        if data["supervisor_id"] is not None:
            validate_supervisor(
                db, data["supervisor_id"], excluding_employee_id=employee_id  # type: ignore[arg-type]
            )
        if data["date_of_birth"] is not None:
            validate_dob(data["date_of_birth"])  # type: ignore[arg-type]
        # A blank SSN field on edit means "keep the current value" (the form
        # never renders the real SSN back). Only validate when a new one was
        # actually entered.
        if data["ssn"] is not None:
            validate_ssn_format(data["ssn"])  # type: ignore[arg-type]
            validate_ssn_unique(
                db, data["ssn"], excluding_employee_id=employee_id  # type: ignore[arg-type]
            )
        if data["employee_number"]:
            validate_employee_number_unique(
                db, data["employee_number"], excluding_employee_id=employee_id  # type: ignore[arg-type]
            )
    except (HTTPException, ValueError) as exc:
        msg = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _error(msg)

    # Custom attributes: merge cf_<key> inputs into the existing bag.
    merged_custom, cf_errors = _apply_custom_fields(db, form, employee)
    if cf_errors:
        return _error("; ".join(cf_errors))

    # Preserve the existing SSN when the field was left blank on edit.
    if data["ssn"] is None:
        data["ssn"] = employee.ssn

    # The static-reference-manager flag is only editable while the feature is
    # enabled; otherwise preserve whatever the record already had.
    if not reference_managers.is_enabled(db):
        data["is_reference_manager"] = employee.is_reference_manager

    for field, value in data.items():
        setattr(employee, field, value)
    employee.custom_fields = merged_custom
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = str(exc.orig).lower()
        if "ssn" in msg:
            return _error("An employee with this Social Security Number already exists.")
        if "employee_number" in msg or "unique" in msg:
            return _error("That employee number is in use by another employee.")
        return _error(f"Database error: {exc.orig}")

    log.info("ui_employee_updated", extra={"employee_id": employee.id, "by": user.username})
    record_event(
        category="employee",
        event_type="employee.updated",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        target_id=employee.id,
        target_label=_emp_label(employee),
        message=f"Updated employee {_emp_label(employee)}",
        detail={"surface": "ui", "fields": list(data.keys())},
        request=request,
    )
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
    user: AppUser = Depends(require_employee_manager),
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
        record_event(
            category="employee",
            event_type="employee.archived",
            actor_type="user",
            actor_label=user.username,
            actor_id=user.id,
            target_type="employee",
            target_id=employee.id,
            target_label=_emp_label(employee),
            message=f"Archived employee {_emp_label(employee)}",
            detail={"surface": "ui"},
            request=request,
        )
        flash(request, f"Employee {employee.employee_number} archived.", "success")
    return RedirectResponse(url="/ui/employees", status_code=303)


@router.post("/{employee_id}/restore")
def restore_employee(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_employee_manager),
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
        record_event(
            category="employee",
            event_type="employee.restored",
            actor_type="user",
            actor_label=user.username,
            actor_id=user.id,
            target_type="employee",
            target_id=employee.id,
            target_label=_emp_label(employee),
            message=f"Restored employee {_emp_label(employee)}",
            detail={"surface": "ui"},
            request=request,
        )
        flash(request, f"Employee {employee.employee_number} restored.", "success")
    return RedirectResponse(url="/ui/employees?view=archived", status_code=303)


# ---------------------------------------------------------------------------
# Purge archived (admin-only cleanup)
# ---------------------------------------------------------------------------


@router.post("/purge-archived")
def purge_archived_employees(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    """Permanently delete every archived employee. Admin-only, irreversible."""
    archived = (
        db.query(Employee)
        .filter(Employee.is_archived.is_(True))
        .order_by(Employee.last_name, Employee.first_name)
        .all()
    )
    count = len(archived)

    if count == 0:
        flash(request, "There are no archived employees to delete.", "info")
        return RedirectResponse(url="/ui/employees?view=archived", status_code=303)

    archived_ids = [e.id for e in archived]
    # Snapshot labels before deletion for the audit detail.
    purged_labels = [_emp_label(e) for e in archived]

    # FOREIGN KEY enforcement is on (see app/db.py), and supervisor_id is a
    # self-referential FK. An employee we're keeping may still point at an
    # archived supervisor (e.g., a supervisor archived after assignment), and
    # archived employees may reference one another. Null out any supervisor_id
    # that references a to-be-deleted row so the deletes don't hit a FK error.
    (
        db.query(Employee)
        .filter(Employee.supervisor_id.in_(archived_ids))
        .update({Employee.supervisor_id: None}, synchronize_session=False)
    )

    for employee in archived:
        db.delete(employee)
    db.commit()

    log.info(
        "ui_employees_purged_archived",
        extra={"count": count, "by": user.username},
    )
    record_event(
        category="employee",
        event_type="employee.purged_archived",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        message=f"Deleted {count} archived employee{'' if count == 1 else 's'}",
        detail={
            "surface": "ui",
            "count": count,
            "employees": purged_labels,
        },
        request=request,
    )
    flash(
        request,
        f"Deleted {count} archived employee{'' if count == 1 else 's'}.",
        "success",
    )
    return RedirectResponse(url="/ui/employees?view=archived", status_code=303)
