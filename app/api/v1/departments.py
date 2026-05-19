"""Department CRUD endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import raise_conflict_if_referenced
from app.db import get_db
from app.models import Department, Employee, JobTitle
from app.schemas.lookups import DepartmentCreate, DepartmentOut, DepartmentUpdate
from app.services.auth import Principal, get_authenticated_principal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/departments", tags=["lookups"])


@router.get("/", response_model=list[DepartmentOut])
def list_departments(
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> list[Department]:
    query = db.query(Department)
    if is_active is not None:
        query = query.filter(Department.is_active == is_active)
    return query.order_by(Department.name).all()


@router.get("/{dept_id}", response_model=DepartmentOut)
def get_department(
    dept_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> Department:
    dept = db.get(Department, dept_id)
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found."
        )
    return dept


@router.post("/", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(
    body: DepartmentCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Department:
    dept = Department(name=body.name, is_active=body.is_active)
    db.add(dept)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department '{body.name}' already exists.",
        ) from None
    db.refresh(dept)
    log.info(
        "department_created",
        extra={"department_id": dept.id, "by": principal.identifier},
    )
    return dept


@router.patch("/{dept_id}", response_model=DepartmentOut)
def update_department(
    dept_id: int,
    body: DepartmentUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Department:
    dept = db.get(Department, dept_id)
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found."
        )
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(dept, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A department with that name already exists.",
        ) from None
    db.refresh(dept)
    log.info(
        "department_updated",
        extra={"department_id": dept.id, "by": principal.identifier},
    )
    return dept


@router.delete("/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    dept_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> None:
    dept = db.get(Department, dept_id)
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found."
        )
    raise_conflict_if_referenced(
        db=db,
        target_label=f"department '{dept.name}'",
        references=[
            ("employees", Employee, Employee.department_id, dept_id),
            ("job titles", JobTitle, JobTitle.department_id, dept_id),
        ],
    )
    db.delete(dept)
    db.commit()
    log.info(
        "department_deleted",
        extra={"department_id": dept_id, "by": principal.identifier},
    )
