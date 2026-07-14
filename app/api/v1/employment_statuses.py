"""Employment status CRUD endpoints.

System rows (is_system=true) cannot be deleted or have their value changed,
since IGA systems may depend on the specific numeric values 0/1 for active/inactive.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import (
    raise_conflict_if_referenced,
    raise_conflict_system_row,
)
from app.db import get_db
from app.models import Employee, EmploymentStatus
from app.schemas.lookups import (
    EmploymentStatusCreate,
    EmploymentStatusOut,
    EmploymentStatusUpdate,
)
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/employment-statuses", tags=["lookups"])


@router.get("/", response_model=list[EmploymentStatusOut])
def list_statuses(
    is_active_status: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("lookups:read")),
) -> list[EmploymentStatus]:
    query = db.query(EmploymentStatus)
    if is_active_status is not None:
        query = query.filter(EmploymentStatus.is_active_status == is_active_status)
    return query.order_by(EmploymentStatus.value).all()


@router.get("/{status_id}", response_model=EmploymentStatusOut)
def get_status(
    status_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("lookups:read")),
) -> EmploymentStatus:
    s = db.get(EmploymentStatus, status_id)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employment status not found."
        )
    return s


@router.post("/", response_model=EmploymentStatusOut, status_code=status.HTTP_201_CREATED)
def create_status(
    body: EmploymentStatusCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> EmploymentStatus:
    new_status = EmploymentStatus(
        label=body.label,
        value=body.value,
        is_active_status=body.is_active_status,
        is_system=False,  # User-created rows are never system rows
    )
    db.add(new_status)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Employment status '{body.label}' already exists.",
        ) from None
    db.refresh(new_status)
    log.info(
        "employment_status_created",
        extra={"status_id": new_status.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.employment_status.created",
        **principal_actor(principal),
        target_type="employment_status",
        target_id=new_status.id,
        target_label=new_status.label,
        message=f"Created employment status '{new_status.label}'",
        detail={"surface": "api"},
    )
    return new_status


@router.patch("/{status_id}", response_model=EmploymentStatusOut)
def update_status(
    status_id: int,
    body: EmploymentStatusUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> EmploymentStatus:
    s = db.get(EmploymentStatus, status_id)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employment status not found."
        )

    data = body.model_dump(exclude_unset=True)
    # System rows: the numeric value cannot be changed (IGA systems may depend on it)
    if s.is_system and "value" in data and data["value"] != s.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot change the numeric value of system status '{s.label}'. "
                "Integrating systems may depend on these values."
            ),
        )

    for field, value in data.items():
        setattr(s, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An employment status with that label already exists.",
        ) from None
    db.refresh(s)
    log.info(
        "employment_status_updated",
        extra={"status_id": s.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.employment_status.updated",
        **principal_actor(principal),
        target_type="employment_status",
        target_id=s.id,
        target_label=s.label,
        message=f"Updated employment status '{s.label}'",
        detail={"surface": "api"},
    )
    return s


@router.delete("/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_status(
    status_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> None:
    s = db.get(EmploymentStatus, status_id)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employment status not found."
        )
    if s.is_system:
        raise_conflict_system_row(f"employment status '{s.label}'")

    raise_conflict_if_referenced(
        db=db,
        target_label=f"employment status '{s.label}'",
        references=[
            ("employees", Employee, Employee.employment_status_id, status_id),
        ],
    )

    status_label = s.label
    db.delete(s)
    db.commit()
    log.info(
        "employment_status_deleted",
        extra={"status_id": status_id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.employment_status.deleted",
        **principal_actor(principal),
        target_type="employment_status",
        target_id=status_id,
        target_label=status_label,
        message=f"Deleted employment status '{status_label}'",
        detail={"surface": "api"},
    )
