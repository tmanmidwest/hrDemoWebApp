"""Console-user (AppUser) management endpoints.

These govern the accounts that sign in to the web UI and their roles. Access
requires a bearer token (API key or OAuth client) — the same surface as the
employees data API — so IGA/automation can provision and govern console users.

Accounts can be disabled (soft-off via ``is_active``) but never deleted through
the API. The seeded admin cannot be disabled or demoted, guaranteeing at least
one active admin remains.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import AppUser, Role, RoleAssignment
from app.schemas.roles import RoleAssignmentOut, RoleGrant
from app.schemas.users import UserCreate, UserOut, UserUpdate
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope
from app.services.passwords import hash_password

log = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


def _get_or_404(db: Session, user_id: int) -> AppUser:
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.get("/", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("users:read")),
) -> list[AppUser]:
    """List all console accounts."""
    return db.query(AppUser).order_by(AppUser.username).all()


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("users:read")),
) -> AppUser:
    """Get a single console account by ID."""
    return _get_or_404(db, user_id)


@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("users:write")),
) -> AppUser:
    """Create a local (password) console account."""
    user = AppUser(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        role=body.role.value,
        is_active=True,
        is_seeded=False,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username.strip()}' is already taken.",
        ) from None
    db.refresh(user)

    log.info(
        "api_user_created",
        extra={"user_id": user.id, "username": user.username, "role": user.role,
               "by": principal.identifier},
    )
    record_event(
        category="app_user",
        event_type="app_user.created",
        **principal_actor(principal),
        target_type="app_user",
        target_id=user.id,
        target_label=user.username,
        message=f"Created user '{user.username}' ({user.role_label})",
        detail={"surface": "api", "role": user.role},
        request=request,
    )
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("users:write")),
) -> AppUser:
    """Update a console account's username, password, and/or role."""
    user = _get_or_404(db, user_id)

    if body.role is not None and user.is_seeded and body.role.value != user.role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The seeded admin's role cannot be changed.",
        )

    changed: list[str] = []
    if body.username is not None:
        user.username = body.username.strip()
        changed.append("username")
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        changed.append("password")
    if body.role is not None:
        user.role = body.role.value
        changed.append("role")

    if not changed:
        return user

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username is already taken.",
        ) from None
    db.refresh(user)

    log.info(
        "api_user_updated",
        extra={"user_id": user.id, "fields": changed, "by": principal.identifier},
    )
    record_event(
        category="app_user",
        event_type="app_user.updated",
        **principal_actor(principal),
        target_type="app_user",
        target_id=user.id,
        target_label=user.username,
        message=f"Updated user '{user.username}' ({', '.join(changed)})",
        detail={"surface": "api", "fields": changed},
        request=request,
    )
    return user


@router.post("/{user_id}/disable", response_model=UserOut)
def disable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("users:write")),
) -> AppUser:
    """Disable a console account so it can no longer sign in."""
    user = _get_or_404(db, user_id)
    if user.is_seeded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The seeded admin cannot be disabled.",
        )
    if user.is_active:
        user.is_active = False
        db.commit()
        db.refresh(user)
        log.info("api_user_disabled", extra={"user_id": user.id, "by": principal.identifier})
        record_event(
            category="app_user",
            event_type="app_user.disabled",
            **principal_actor(principal),
            target_type="app_user",
            target_id=user.id,
            target_label=user.username,
            message=f"Disabled user '{user.username}'",
            detail={"surface": "api"},
            request=request,
        )
    return user


@router.post("/{user_id}/enable", response_model=UserOut)
def enable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("users:write")),
) -> AppUser:
    """Re-enable a disabled console account."""
    user = _get_or_404(db, user_id)
    if not user.is_active:
        user.is_active = True
        db.commit()
        db.refresh(user)
        log.info("api_user_enabled", extra={"user_id": user.id, "by": principal.identifier})
        record_event(
            category="app_user",
            event_type="app_user.enabled",
            **principal_actor(principal),
            target_type="app_user",
            target_id=user.id,
            target_label=user.username,
            message=f"Enabled user '{user.username}'",
            detail={"surface": "api"},
            request=request,
        )
    return user


# ---------------------------------------------------------------------------
# Role assignments — the IGA provisioning / deprovisioning surface.
#
# Reads use `roles:read`, writes use `roles:write`, so an IGA connector can be
# granted exactly the roles scopes without any employee/user write access.
# ---------------------------------------------------------------------------


@router.get("/{user_id}/roles", response_model=list[RoleAssignmentOut])
def list_user_roles(
    user_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("roles:read")),
) -> list[RoleAssignment]:
    """List the access roles currently granted to a console account."""
    _get_or_404(db, user_id)
    return (
        db.query(RoleAssignment)
        .options(joinedload(RoleAssignment.user), joinedload(RoleAssignment.role))
        .filter(RoleAssignment.user_id == user_id)
        .order_by(RoleAssignment.id)
        .all()
    )


@router.post(
    "/{user_id}/roles",
    response_model=RoleAssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
def grant_user_role(
    user_id: int,
    body: RoleGrant,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("roles:write")),
) -> RoleAssignment:
    """Grant (provision) an access role to a console account."""
    user = _get_or_404(db, user_id)

    role = db.get(Role, body.role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found."
        )
    if not role.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{role.name}' is inactive and cannot be granted.",
        )

    assignment = RoleAssignment(user_id=user.id, role_id=role.id)
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{user.username}' already holds role '{role.name}'.",
        ) from None
    db.refresh(assignment)

    log.info(
        "role_granted",
        extra={"user_id": user.id, "role_id": role.id, "by": principal.identifier},
    )
    record_event(
        category="role",
        event_type="role.granted",
        **principal_actor(principal),
        target_type="app_user",
        target_id=user.id,
        target_label=user.username,
        message=f"Granted role '{role.name}' to '{user.username}'",
        detail={"surface": "api", "role_id": role.id, "role_name": role.name},
        request=request,
    )
    return assignment


@router.delete(
    "/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_user_role(
    user_id: int,
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("roles:write")),
) -> None:
    """Revoke (deprovision) an access role from a console account."""
    user = _get_or_404(db, user_id)
    assignment = (
        db.query(RoleAssignment)
        .filter(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role_id == role_id,
        )
        .one_or_none()
    )
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This user does not hold that role.",
        )
    role = db.get(Role, role_id)
    role_name = role.name if role is not None else str(role_id)
    db.delete(assignment)
    db.commit()

    log.info(
        "role_revoked",
        extra={"user_id": user_id, "role_id": role_id, "by": principal.identifier},
    )
    record_event(
        category="role",
        event_type="role.revoked",
        **principal_actor(principal),
        target_type="app_user",
        target_id=user.id,
        target_label=user.username,
        message=f"Revoked role '{role_name}' from '{user.username}'",
        detail={"surface": "api", "role_id": role_id, "role_name": role_name},
        request=request,
    )
