"""Job title CRUD endpoints. Filterable by department for dependent dropdowns."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import raise_conflict_if_referenced
from app.db import get_db
from app.models import Department, Employee, JobTitle
from app.schemas.lookups import JobTitleCreate, JobTitleOut, JobTitleUpdate
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/job-titles", tags=["lookups"])


def _validate_department_exists(db: Session, department_id: int) -> None:
    if db.get(Department, department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"department_id {department_id} does not exist.",
        )


@router.get("/", response_model=list[JobTitleOut])
def list_job_titles(
    department_id: int | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("lookups:read")),
) -> list[JobTitle]:
    query = db.query(JobTitle)
    if department_id is not None:
        query = query.filter(JobTitle.department_id == department_id)
    if is_active is not None:
        query = query.filter(JobTitle.is_active == is_active)
    return query.order_by(JobTitle.name).all()


@router.get("/{title_id}", response_model=JobTitleOut)
def get_job_title(
    title_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("lookups:read")),
) -> JobTitle:
    title = db.get(JobTitle, title_id)
    if title is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job title not found."
        )
    return title


@router.post("/", response_model=JobTitleOut, status_code=status.HTTP_201_CREATED)
def create_job_title(
    body: JobTitleCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> JobTitle:
    _validate_department_exists(db, body.department_id)
    title = JobTitle(
        department_id=body.department_id,
        name=body.name,
        is_active=body.is_active,
    )
    db.add(title)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job title '{body.name}' already exists in this department.",
        ) from None
    db.refresh(title)
    log.info(
        "job_title_created",
        extra={"title_id": title.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.job_title.created",
        **principal_actor(principal),
        target_type="job_title",
        target_id=title.id,
        target_label=title.name,
        message=f"Created job title '{title.name}'",
        detail={"surface": "api"},
    )
    return title


@router.patch("/{title_id}", response_model=JobTitleOut)
def update_job_title(
    title_id: int,
    body: JobTitleUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> JobTitle:
    title = db.get(JobTitle, title_id)
    if title is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job title not found."
        )
    data = body.model_dump(exclude_unset=True)
    if "department_id" in data:
        _validate_department_exists(db, data["department_id"])
    for field, value in data.items():
        setattr(title, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That job title name is already used in this department.",
        ) from None
    db.refresh(title)
    log.info(
        "job_title_updated",
        extra={"title_id": title.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.job_title.updated",
        **principal_actor(principal),
        target_type="job_title",
        target_id=title.id,
        target_label=title.name,
        message=f"Updated job title '{title.name}'",
        detail={"surface": "api"},
    )
    return title


@router.delete("/{title_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_title(
    title_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> None:
    title = db.get(JobTitle, title_id)
    if title is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job title not found."
        )
    raise_conflict_if_referenced(
        db=db,
        target_label=f"job title '{title.name}'",
        references=[
            ("employees", Employee, Employee.job_title_id, title_id),
        ],
    )
    title_name = title.name
    db.delete(title)
    db.commit()
    log.info(
        "job_title_deleted",
        extra={"title_id": title_id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.job_title.deleted",
        **principal_actor(principal),
        target_type="job_title",
        target_id=title_id,
        target_label=title_name,
        message=f"Deleted job title '{title_name}'",
        detail={"surface": "api"},
    )
