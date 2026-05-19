"""UI routes for lookup table management (countries, states, statuses, depts, titles)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import count_references
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
from app.ui.dependencies import require_ui_user
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/lookups", tags=["ui"], include_in_schema=False)


@dataclass
class ListConfig:
    title: str
    subtitle: str
    singular: str
    plural: str
    base_path: str


# ===========================================================================
# Countries
# ===========================================================================

COUNTRIES_CONFIG = ListConfig(
    title="Countries",
    subtitle="ISO countries used by employee address records.",
    singular="Country",
    plural="countries",
    base_path="/ui/lookups/countries",
)


@router.get("/countries")
def list_countries(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    rows_raw = db.query(Country).order_by(Country.name).all()
    rows = [
        {
            "id": c.id,
            "is_active": c.is_active,
            "is_system": False,
            "cells": [
                {"value": c.code, "mono": True},
                {"value": c.name, "mono": False},
            ],
        }
        for c in rows_raw
    ]
    return render(
        request,
        "lookups/list.html",
        current_user=user,
        active_subsection="countries",
        config=COUNTRIES_CONFIG,
        headers=["Code", "Name"],
        rows=rows,
    )


@router.get("/countries/new")
def show_new_country(
    request: Request,
    user: AppUser = Depends(require_ui_user),
) -> Response:
    return render(
        request,
        "lookups/country_form.html",
        current_user=user,
        active_subsection="countries",
        row=None,
        form={"is_active": True},
        form_action="/ui/lookups/countries/new",
    )


@router.post("/countries/new")
def create_country(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    form = {"code": code.strip().upper(), "name": name.strip(), "is_active": bool(is_active)}
    if len(form["code"]) != 2:
        return render(
            request,
            "lookups/country_form.html",
            current_user=user,
            active_subsection="countries",
            row=None,
            form=form,
            form_action="/ui/lookups/countries/new",
            error="Code must be exactly 2 letters (ISO-3166-1 alpha-2).",
        )
    c = Country(code=form["code"], name=form["name"], is_active=form["is_active"])
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "lookups/country_form.html",
            current_user=user,
            active_subsection="countries",
            row=None,
            form=form,
            form_action="/ui/lookups/countries/new",
            error=f"Country with code '{form['code']}' already exists.",
        )
    flash(request, f"Added country {form['code']}.", "success")
    log.info("ui_country_created", extra={"country_id": c.id, "by": user.username})
    return RedirectResponse(url="/ui/lookups/countries", status_code=303)


@router.get("/countries/{country_id}/edit")
def show_edit_country(
    country_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    c = db.get(Country, country_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Country not found.")
    return render(
        request,
        "lookups/country_form.html",
        current_user=user,
        active_subsection="countries",
        row=c,
        form={"code": c.code, "name": c.name, "is_active": c.is_active},
        form_action=f"/ui/lookups/countries/{c.id}/edit",
    )


@router.post("/countries/{country_id}/edit")
def update_country(
    country_id: int,
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    c = db.get(Country, country_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Country not found.")
    form = {"code": code.strip().upper(), "name": name.strip(), "is_active": bool(is_active)}
    c.code, c.name, c.is_active = form["code"], form["name"], form["is_active"]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "lookups/country_form.html",
            current_user=user,
            active_subsection="countries",
            row=c,
            form=form,
            form_action=f"/ui/lookups/countries/{c.id}/edit",
            error="That code is already used by another country.",
        )
    flash(request, "Country updated.", "success")
    return RedirectResponse(url="/ui/lookups/countries", status_code=303)


@router.post("/countries/{country_id}/delete")
def delete_country(
    country_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    c = db.get(Country, country_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Country not found.")
    emp_refs = count_references(db, Employee, Employee.country_id, country_id)
    state_refs = count_references(db, StateProvince, StateProvince.country_id, country_id)
    if emp_refs or state_refs:
        flash(
            request,
            f"Cannot delete {c.name}: still referenced by {emp_refs} employee(s) and {state_refs} state(s)/province(s). Deactivate it instead.",
            "error",
        )
        return RedirectResponse(url="/ui/lookups/countries", status_code=303)
    db.delete(c)
    db.commit()
    flash(request, f"Deleted {c.name}.", "success")
    log.info("ui_country_deleted", extra={"country_id": country_id, "by": user.username})
    return RedirectResponse(url="/ui/lookups/countries", status_code=303)


# ===========================================================================
# States / Provinces
# ===========================================================================

STATES_CONFIG = ListConfig(
    title="States & Provinces",
    subtitle="State/province subdivisions, linked to a country.",
    singular="State/Province",
    plural="states/provinces",
    base_path="/ui/lookups/states-provinces",
)


@router.get("/states-provinces")
def list_states(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    rows_raw = db.query(StateProvince).order_by(StateProvince.name).all()
    rows = [
        {
            "id": s.id,
            "is_active": s.is_active,
            "is_system": False,
            "cells": [
                {"value": s.country.name if s.country else "—", "mono": False},
                {"value": s.code or "—", "mono": True},
                {"value": s.name, "mono": False},
            ],
        }
        for s in rows_raw
    ]
    return render(
        request,
        "lookups/list.html",
        current_user=user,
        active_subsection="states",
        config=STATES_CONFIG,
        headers=["Country", "Code", "Name"],
        rows=rows,
    )


@router.get("/states-provinces/new")
def show_new_state(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    countries = db.query(Country).filter(Country.is_active.is_(True)).order_by(Country.name).all()
    return render(
        request,
        "lookups/state_form.html",
        current_user=user,
        active_subsection="states",
        row=None,
        form={"is_active": True},
        form_action="/ui/lookups/states-provinces/new",
        countries=countries,
    )


@router.post("/states-provinces/new")
def create_state(
    request: Request,
    country_id: int = Form(...),
    name: str = Form(...),
    code: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    countries = db.query(Country).filter(Country.is_active.is_(True)).order_by(Country.name).all()
    form = {
        "country_id": country_id,
        "name": name.strip(),
        "code": code.strip() if code else None,
        "is_active": bool(is_active),
    }
    if db.get(Country, country_id) is None:
        return render(
            request,
            "lookups/state_form.html",
            current_user=user,
            active_subsection="states",
            row=None,
            form=form,
            form_action="/ui/lookups/states-provinces/new",
            countries=countries,
            error="Selected country no longer exists.",
        )
    s = StateProvince(country_id=country_id, name=form["name"], code=form["code"], is_active=form["is_active"])
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "lookups/state_form.html",
            current_user=user,
            active_subsection="states",
            row=None,
            form=form,
            form_action="/ui/lookups/states-provinces/new",
            countries=countries,
            error="A state/province with that name already exists for this country.",
        )
    flash(request, f"Added {form['name']}.", "success")
    return RedirectResponse(url="/ui/lookups/states-provinces", status_code=303)


@router.get("/states-provinces/{state_id}/edit")
def show_edit_state(
    state_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    s = db.get(StateProvince, state_id)
    if s is None:
        raise HTTPException(status_code=404, detail="State not found.")
    countries = db.query(Country).filter(Country.is_active.is_(True)).order_by(Country.name).all()
    return render(
        request,
        "lookups/state_form.html",
        current_user=user,
        active_subsection="states",
        row=s,
        form={
            "country_id": s.country_id,
            "name": s.name,
            "code": s.code,
            "is_active": s.is_active,
        },
        form_action=f"/ui/lookups/states-provinces/{s.id}/edit",
        countries=countries,
    )


@router.post("/states-provinces/{state_id}/edit")
def update_state(
    state_id: int,
    request: Request,
    country_id: int = Form(...),
    name: str = Form(...),
    code: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    s = db.get(StateProvince, state_id)
    if s is None:
        raise HTTPException(status_code=404, detail="State not found.")
    s.country_id = country_id
    s.name = name.strip()
    s.code = code.strip() if code else None
    s.is_active = bool(is_active)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Update failed (duplicate name for that country?).", "error")
    else:
        flash(request, "State/province updated.", "success")
    return RedirectResponse(url="/ui/lookups/states-provinces", status_code=303)


@router.post("/states-provinces/{state_id}/delete")
def delete_state(
    state_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    s = db.get(StateProvince, state_id)
    if s is None:
        raise HTTPException(status_code=404, detail="State not found.")
    refs = count_references(db, Employee, Employee.state_province_id, state_id)
    if refs:
        flash(request, f"Cannot delete {s.name}: still referenced by {refs} employee(s).", "error")
        return RedirectResponse(url="/ui/lookups/states-provinces", status_code=303)
    db.delete(s)
    db.commit()
    flash(request, f"Deleted {s.name}.", "success")
    return RedirectResponse(url="/ui/lookups/states-provinces", status_code=303)


# ===========================================================================
# Employment Statuses
# ===========================================================================

STATUSES_CONFIG = ListConfig(
    title="Employment Statuses",
    subtitle="Statuses with numeric values sent to integrating IGA systems.",
    singular="Status",
    plural="employment statuses",
    base_path="/ui/lookups/employment-statuses",
)


@router.get("/employment-statuses")
def list_statuses(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    rows_raw = db.query(EmploymentStatus).order_by(EmploymentStatus.value).all()
    rows = [
        {
            "id": s.id,
            "is_active": s.is_active_status,
            "is_system": s.is_system,
            "cells": [
                {"value": s.label, "mono": False},
                {"value": str(s.value), "mono": True},
                {"value": "Yes" if s.is_active_status else "No", "mono": False},
            ],
        }
        for s in rows_raw
    ]
    return render(
        request,
        "lookups/list.html",
        current_user=user,
        active_subsection="statuses",
        config=STATUSES_CONFIG,
        headers=["Label", "Numeric Value", "Counts as Active?"],
        rows=rows,
    )


@router.get("/employment-statuses/new")
def show_new_status(
    request: Request,
    user: AppUser = Depends(require_ui_user),
) -> Response:
    return render(
        request,
        "lookups/status_form.html",
        current_user=user,
        active_subsection="statuses",
        row=None,
        form={"value": None, "is_active_status": False},
        form_action="/ui/lookups/employment-statuses/new",
    )


@router.post("/employment-statuses/new")
def create_status(
    request: Request,
    label: str = Form(...),
    value: int = Form(...),
    is_active_status: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    form = {"label": label.strip(), "value": value, "is_active_status": bool(is_active_status)}
    s = EmploymentStatus(
        label=form["label"], value=form["value"], is_active_status=form["is_active_status"], is_system=False
    )
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "lookups/status_form.html",
            current_user=user,
            active_subsection="statuses",
            row=None,
            form=form,
            form_action="/ui/lookups/employment-statuses/new",
            error=f"Employment status '{form['label']}' already exists.",
        )
    flash(request, f"Added status {form['label']}.", "success")
    return RedirectResponse(url="/ui/lookups/employment-statuses", status_code=303)


@router.get("/employment-statuses/{status_id}/edit")
def show_edit_status(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    s = db.get(EmploymentStatus, status_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Status not found.")
    return render(
        request,
        "lookups/status_form.html",
        current_user=user,
        active_subsection="statuses",
        row=s,
        form={
            "label": s.label,
            "value": s.value,
            "is_active_status": s.is_active_status,
        },
        form_action=f"/ui/lookups/employment-statuses/{s.id}/edit",
    )


@router.post("/employment-statuses/{status_id}/edit")
def update_status(
    status_id: int,
    request: Request,
    label: str = Form(...),
    value: int = Form(...),
    is_active_status: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    s = db.get(EmploymentStatus, status_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Status not found.")
    if s.is_system and value != s.value:
        flash(request, f"Cannot change numeric value of system status '{s.label}'.", "error")
        return RedirectResponse(url=f"/ui/lookups/employment-statuses/{status_id}/edit", status_code=303)
    s.label = label.strip()
    s.value = value
    s.is_active_status = bool(is_active_status)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Update failed (duplicate label?).", "error")
    else:
        flash(request, "Status updated.", "success")
    return RedirectResponse(url="/ui/lookups/employment-statuses", status_code=303)


@router.post("/employment-statuses/{status_id}/delete")
def delete_status(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    s = db.get(EmploymentStatus, status_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Status not found.")
    if s.is_system:
        flash(request, f"Cannot delete system status '{s.label}'.", "error")
        return RedirectResponse(url="/ui/lookups/employment-statuses", status_code=303)
    refs = count_references(db, Employee, Employee.employment_status_id, status_id)
    if refs:
        flash(request, f"Cannot delete '{s.label}': still referenced by {refs} employee(s).", "error")
        return RedirectResponse(url="/ui/lookups/employment-statuses", status_code=303)
    db.delete(s)
    db.commit()
    flash(request, f"Deleted {s.label}.", "success")
    return RedirectResponse(url="/ui/lookups/employment-statuses", status_code=303)


# ===========================================================================
# Departments
# ===========================================================================

DEPT_CONFIG = ListConfig(
    title="Departments",
    subtitle="Organizational departments. Job titles hang off these.",
    singular="Department",
    plural="departments",
    base_path="/ui/lookups/departments",
)


@router.get("/departments")
def list_departments(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    rows_raw = db.query(Department).order_by(Department.name).all()
    rows = [
        {
            "id": d.id,
            "is_active": d.is_active,
            "is_system": False,
            "cells": [{"value": d.name, "mono": False}],
        }
        for d in rows_raw
    ]
    return render(
        request,
        "lookups/list.html",
        current_user=user,
        active_subsection="departments",
        config=DEPT_CONFIG,
        headers=["Name"],
        rows=rows,
    )


@router.get("/departments/new")
def show_new_dept(
    request: Request,
    user: AppUser = Depends(require_ui_user),
) -> Response:
    return render(
        request,
        "lookups/department_form.html",
        current_user=user,
        active_subsection="departments",
        row=None,
        form={"is_active": True},
        form_action="/ui/lookups/departments/new",
    )


@router.post("/departments/new")
def create_dept(
    request: Request,
    name: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    form = {"name": name.strip(), "is_active": bool(is_active)}
    d = Department(name=form["name"], is_active=form["is_active"])
    db.add(d)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "lookups/department_form.html",
            current_user=user,
            active_subsection="departments",
            row=None,
            form=form,
            form_action="/ui/lookups/departments/new",
            error=f"Department '{form['name']}' already exists.",
        )
    flash(request, f"Added {form['name']}.", "success")
    return RedirectResponse(url="/ui/lookups/departments", status_code=303)


@router.get("/departments/{dept_id}/edit")
def show_edit_dept(
    dept_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    return render(
        request,
        "lookups/department_form.html",
        current_user=user,
        active_subsection="departments",
        row=d,
        form={"name": d.name, "is_active": d.is_active},
        form_action=f"/ui/lookups/departments/{d.id}/edit",
    )


@router.post("/departments/{dept_id}/edit")
def update_dept(
    dept_id: int,
    request: Request,
    name: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    d.name = name.strip()
    d.is_active = bool(is_active)
    try:
        db.commit()
        flash(request, "Department updated.", "success")
    except IntegrityError:
        db.rollback()
        flash(request, "Update failed (duplicate name?).", "error")
    return RedirectResponse(url="/ui/lookups/departments", status_code=303)


@router.post("/departments/{dept_id}/delete")
def delete_dept(
    dept_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    emp_refs = count_references(db, Employee, Employee.department_id, dept_id)
    title_refs = count_references(db, JobTitle, JobTitle.department_id, dept_id)
    if emp_refs or title_refs:
        flash(
            request,
            f"Cannot delete '{d.name}': still referenced by {emp_refs} employee(s) and {title_refs} job title(s).",
            "error",
        )
        return RedirectResponse(url="/ui/lookups/departments", status_code=303)
    db.delete(d)
    db.commit()
    flash(request, f"Deleted {d.name}.", "success")
    return RedirectResponse(url="/ui/lookups/departments", status_code=303)


# ===========================================================================
# Job Titles
# ===========================================================================

TITLES_CONFIG = ListConfig(
    title="Job Titles",
    subtitle="Job titles linked to a parent department.",
    singular="Job Title",
    plural="job titles",
    base_path="/ui/lookups/job-titles",
)


@router.get("/job-titles")
def list_titles(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    rows_raw = (
        db.query(JobTitle).join(JobTitle.department).order_by(Department.name, JobTitle.name).all()
    )
    rows = [
        {
            "id": t.id,
            "is_active": t.is_active,
            "is_system": False,
            "cells": [
                {"value": t.department.name, "mono": False},
                {"value": t.name, "mono": False},
            ],
        }
        for t in rows_raw
    ]
    return render(
        request,
        "lookups/list.html",
        current_user=user,
        active_subsection="job_titles",
        config=TITLES_CONFIG,
        headers=["Department", "Name"],
        rows=rows,
    )


@router.get("/job-titles/new")
def show_new_title(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    departments = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    return render(
        request,
        "lookups/job_title_form.html",
        current_user=user,
        active_subsection="job_titles",
        row=None,
        form={"is_active": True},
        form_action="/ui/lookups/job-titles/new",
        departments=departments,
    )


@router.post("/job-titles/new")
def create_title(
    request: Request,
    department_id: int = Form(...),
    name: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    departments = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    form = {"department_id": department_id, "name": name.strip(), "is_active": bool(is_active)}
    if db.get(Department, department_id) is None:
        return render(
            request,
            "lookups/job_title_form.html",
            current_user=user,
            active_subsection="job_titles",
            row=None,
            form=form,
            form_action="/ui/lookups/job-titles/new",
            departments=departments,
            error="Department not found.",
        )
    t = JobTitle(department_id=department_id, name=form["name"], is_active=form["is_active"])
    db.add(t)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "lookups/job_title_form.html",
            current_user=user,
            active_subsection="job_titles",
            row=None,
            form=form,
            form_action="/ui/lookups/job-titles/new",
            departments=departments,
            error=f"Job title '{form['name']}' already exists in that department.",
        )
    flash(request, f"Added {form['name']}.", "success")
    return RedirectResponse(url="/ui/lookups/job-titles", status_code=303)


@router.get("/job-titles/{title_id}/edit")
def show_edit_title(
    title_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    t = db.get(JobTitle, title_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Job title not found.")
    departments = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    return render(
        request,
        "lookups/job_title_form.html",
        current_user=user,
        active_subsection="job_titles",
        row=t,
        form={"department_id": t.department_id, "name": t.name, "is_active": t.is_active},
        form_action=f"/ui/lookups/job-titles/{t.id}/edit",
        departments=departments,
    )


@router.post("/job-titles/{title_id}/edit")
def update_title(
    title_id: int,
    request: Request,
    department_id: int = Form(...),
    name: str = Form(...),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    t = db.get(JobTitle, title_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Job title not found.")
    t.department_id = department_id
    t.name = name.strip()
    t.is_active = bool(is_active)
    try:
        db.commit()
        flash(request, "Job title updated.", "success")
    except IntegrityError:
        db.rollback()
        flash(request, "Update failed (duplicate?).", "error")
    return RedirectResponse(url="/ui/lookups/job-titles", status_code=303)


@router.post("/job-titles/{title_id}/delete")
def delete_title(
    title_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    t = db.get(JobTitle, title_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Job title not found.")
    refs = count_references(db, Employee, Employee.job_title_id, title_id)
    if refs:
        flash(request, f"Cannot delete '{t.name}': still referenced by {refs} employee(s).", "error")
        return RedirectResponse(url="/ui/lookups/job-titles", status_code=303)
    db.delete(t)
    db.commit()
    flash(request, f"Deleted {t.name}.", "success")
    return RedirectResponse(url="/ui/lookups/job-titles", status_code=303)
