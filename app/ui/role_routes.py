"""UI routes for the access-role catalog and role assignments.

Admin-only. Mirrors the lookup CRUD pattern (server-rendered Jinja, plain form
POST + 303 redirect). The catalog manages named access roles; the assignments
page grants/revokes those roles to console accounts (`AppUser`) — the same
provisioning surface the IGA REST API drives, exposed for demo/manual use.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.v1._helpers import count_references
from app.db import get_db
from app.models import AppUser, Role, RoleAssignment
from app.services.audit import record_event
from app.ui.dependencies import require_admin
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/roles", tags=["ui"], include_in_schema=False)


def _actor(user: AppUser) -> dict:
    return {"actor_type": "user", "actor_label": user.username, "actor_id": user.id}


# ===========================================================================
# Catalog
# ===========================================================================


@router.get("")
def list_roles(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    roles = db.query(Role).order_by(Role.name).all()
    counts = {
        r.id: count_references(db, RoleAssignment, RoleAssignment.role_id, r.id)
        for r in roles
    }
    return render(
        request,
        "roles/list.html",
        current_user=user,
        active_section="roles",
        active_subsection="roles_catalog",
        roles=roles,
        assignment_counts=counts,
    )


@router.get("/new")
def show_new_role(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "roles/role_form.html",
        current_user=user,
        active_section="roles",
        row=None,
        form={"is_active": True},
        form_action="/ui/roles/new",
    )


@router.post("/new")
def create_role(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    form = {
        "name": name.strip(),
        "description": (description or "").strip() or None,
        "is_active": bool(is_active),
    }
    role = Role(
        name=form["name"], description=form["description"], is_active=form["is_active"]
    )
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "roles/role_form.html",
            current_user=user,
            active_section="roles",
            row=None,
            form=form,
            form_action="/ui/roles/new",
            error=f"Role '{form['name']}' already exists.",
        )
    flash(request, f"Added role '{role.name}'.", "success")
    record_event(
        category="role",
        event_type="role.created",
        **_actor(user),
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        message=f"Created role '{role.name}'",
        detail={"surface": "ui"},
        request=request,
    )
    return RedirectResponse(url="/ui/roles", status_code=303)


@router.get("/{role_id}/edit")
def show_edit_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found.")
    return render(
        request,
        "roles/role_form.html",
        current_user=user,
        active_section="roles",
        row=role,
        form={
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active,
        },
        form_action=f"/ui/roles/{role.id}/edit",
    )


@router.post("/{role_id}/edit")
def update_role(
    role_id: int,
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found.")
    role.name = name.strip()
    role.description = (description or "").strip() or None
    role.is_active = bool(is_active)
    try:
        db.commit()
        flash(request, "Role updated.", "success")
        record_event(
            category="role",
            event_type="role.updated",
            **_actor(user),
            target_type="role",
            target_id=role.id,
            target_label=role.name,
            message=f"Updated role '{role.name}'",
            detail={"surface": "ui"},
            request=request,
        )
    except IntegrityError:
        db.rollback()
        flash(request, "Update failed (duplicate name?).", "error")
    return RedirectResponse(url="/ui/roles", status_code=303)


@router.post("/{role_id}/delete")
def delete_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found.")
    held = count_references(db, RoleAssignment, RoleAssignment.role_id, role_id)
    if held > 0:
        flash(
            request,
            f"Can't delete '{role.name}': it's still granted to {held} "
            f"account(s). Revoke it or set it inactive first.",
            "error",
        )
        return RedirectResponse(url="/ui/roles", status_code=303)
    role_name = role.name
    db.delete(role)
    db.commit()
    flash(request, f"Deleted role '{role_name}'.", "success")
    record_event(
        category="role",
        event_type="role.deleted",
        **_actor(user),
        target_type="role",
        target_id=role_id,
        target_label=role_name,
        message=f"Deleted role '{role_name}'",
        detail={"surface": "ui"},
        request=request,
    )
    return RedirectResponse(url="/ui/roles", status_code=303)


# ===========================================================================
# Assignments (provision / deprovision)
# ===========================================================================


@router.get("/assignments")
def list_assignments(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    assignments = (
        db.query(RoleAssignment)
        .options(joinedload(RoleAssignment.user), joinedload(RoleAssignment.role))
        .join(RoleAssignment.user)
        .join(RoleAssignment.role)
        .order_by(AppUser.username, Role.name)
        .all()
    )
    users = db.query(AppUser).filter(AppUser.is_active).order_by(AppUser.username).all()
    roles = db.query(Role).filter(Role.is_active).order_by(Role.name).all()
    return render(
        request,
        "roles/assignments.html",
        current_user=user,
        active_section="roles",
        active_subsection="roles_assignments",
        assignments=assignments,
        users=users,
        roles=roles,
    )


@router.post("/assignments/grant")
def grant_assignment(
    request: Request,
    user_id: int = Form(...),
    role_id: int = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    account = db.get(AppUser, user_id)
    role = db.get(Role, role_id)
    if account is None or role is None:
        flash(request, "That user or role no longer exists.", "error")
        return RedirectResponse(url="/ui/roles/assignments", status_code=303)
    if not role.is_active:
        flash(request, f"Role '{role.name}' is inactive and can't be granted.", "error")
        return RedirectResponse(url="/ui/roles/assignments", status_code=303)

    db.add(RoleAssignment(user_id=account.id, role_id=role.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(
            request,
            f"'{account.username}' already holds '{role.name}'.",
            "error",
        )
        return RedirectResponse(url="/ui/roles/assignments", status_code=303)
    flash(request, f"Granted '{role.name}' to '{account.username}'.", "success")
    record_event(
        category="role",
        event_type="role.granted",
        **_actor(user),
        target_type="app_user",
        target_id=account.id,
        target_label=account.username,
        message=f"Granted role '{role.name}' to '{account.username}'",
        detail={"surface": "ui", "role_id": role.id, "role_name": role.name},
        request=request,
    )
    return RedirectResponse(url="/ui/roles/assignments", status_code=303)


@router.post("/assignments/{assignment_id}/revoke")
def revoke_assignment(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    assignment = (
        db.query(RoleAssignment)
        .options(joinedload(RoleAssignment.user), joinedload(RoleAssignment.role))
        .filter(RoleAssignment.id == assignment_id)
        .one_or_none()
    )
    if assignment is None:
        flash(request, "That assignment no longer exists.", "error")
        return RedirectResponse(url="/ui/roles/assignments", status_code=303)
    username = assignment.user.username
    role_name = assignment.role.name
    account_id = assignment.user_id
    role_id = assignment.role_id
    db.delete(assignment)
    db.commit()
    flash(request, f"Revoked '{role_name}' from '{username}'.", "success")
    record_event(
        category="role",
        event_type="role.revoked",
        **_actor(user),
        target_type="app_user",
        target_id=account_id,
        target_label=username,
        message=f"Revoked role '{role_name}' from '{username}'",
        detail={"surface": "ui", "role_id": role_id, "role_name": role_name},
        request=request,
    )
    return RedirectResponse(url="/ui/roles/assignments", status_code=303)
