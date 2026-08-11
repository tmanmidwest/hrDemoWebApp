"""Access-role catalog and assignment-reconciliation endpoints.

The catalog (`/roles`) is CRUD over the named access roles an IGA platform
governs. The flat assignment feed (`/roles/assignments`) lets a connector pull
every current grant in one call — with `?updated_since=` for incremental
reconciliation. Grants themselves are created and revoked per-user under
`/users/{id}/roles` (see app/api/v1/users.py).

All endpoints require a bearer token (API key or OAuth client) carrying the
`roles:read` / `roles:write` scopes.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.v1._helpers import raise_conflict_if_referenced
from app.db import get_db
from app.models import Role, RoleAssignment
from app.schemas.roles import (
    RoleAssignmentOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/roles", tags=["roles"])


def _get_or_404(db: Session, role_id: int) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found."
        )
    return role


@router.get("/", response_model=list[RoleOut])
def list_roles(
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("roles:read")),
) -> list[Role]:
    """List the access-role catalog."""
    query = db.query(Role)
    if is_active is not None:
        query = query.filter(Role.is_active == is_active)
    return query.order_by(Role.name).all()


@router.get("/assignments", response_model=list[RoleAssignmentOut])
def list_role_assignments(
    user_id: int | None = Query(
        default=None, description="Only assignments for this console account."
    ),
    role_id: int | None = Query(
        default=None, description="Only assignments of this role."
    ),
    updated_since: datetime | None = Query(
        default=None,
        description=(
            "ISO-8601 datetime. Returns only assignments granted or changed at "
            "or after this time — for incremental IGA reconciliation."
        ),
    ),
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("roles:read")),
) -> list[RoleAssignment]:
    """Flat feed of every role grant, for pull-based reconciliation.

    Declared before ``/{role_id}`` so the literal path wins over the dynamic
    integer segment.
    """
    query = db.query(RoleAssignment).options(
        joinedload(RoleAssignment.user), joinedload(RoleAssignment.role)
    )
    if user_id is not None:
        query = query.filter(RoleAssignment.user_id == user_id)
    if role_id is not None:
        query = query.filter(RoleAssignment.role_id == role_id)
    if updated_since is not None:
        query = query.filter(RoleAssignment.updated_at >= updated_since)
    return query.order_by(RoleAssignment.id).all()


@router.get("/{role_id}", response_model=RoleOut)
def get_role(
    role_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("roles:read")),
) -> Role:
    """Get a single access role by ID."""
    return _get_or_404(db, role_id)


@router.post("/", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    body: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("roles:write")),
) -> Role:
    """Create an access role."""
    role = Role(
        name=body.name.strip(),
        description=body.description,
        is_active=body.is_active,
    )
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role '{body.name.strip()}' already exists.",
        ) from None
    db.refresh(role)
    log.info("role_created", extra={"role_id": role.id, "by": principal.identifier})
    record_event(
        category="role",
        event_type="role.created",
        **principal_actor(principal),
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        message=f"Created role '{role.name}'",
        detail={"surface": "api"},
        request=request,
    )
    return role


@router.patch("/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    body: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("roles:write")),
) -> Role:
    """Update an access role."""
    role = _get_or_404(db, role_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    for field, value in data.items():
        setattr(role, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A role with that name already exists.",
        ) from None
    db.refresh(role)
    log.info("role_updated", extra={"role_id": role.id, "by": principal.identifier})
    record_event(
        category="role",
        event_type="role.updated",
        **principal_actor(principal),
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        message=f"Updated role '{role.name}'",
        detail={"surface": "api", "fields": sorted(data.keys())},
        request=request,
    )
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("roles:write")),
) -> None:
    """Delete an access role.

    Blocked while the role is still granted to any account — deactivate it
    (``is_active=false``) or revoke the assignments first.
    """
    role = _get_or_404(db, role_id)
    raise_conflict_if_referenced(
        db=db,
        target_label=f"role '{role.name}'",
        references=[
            ("role assignments", RoleAssignment, RoleAssignment.role_id, role_id),
        ],
    )
    role_name = role.name
    db.delete(role)
    db.commit()
    log.info("role_deleted", extra={"role_id": role_id, "by": principal.identifier})
    record_event(
        category="role",
        event_type="role.deleted",
        **principal_actor(principal),
        target_type="role",
        target_id=role_id,
        target_label=role_name,
        message=f"Deleted role '{role_name}'",
        detail={"surface": "api"},
        request=request,
    )
