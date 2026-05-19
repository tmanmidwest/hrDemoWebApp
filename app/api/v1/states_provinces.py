"""States/provinces CRUD endpoints.

Supports filtering by `country_id` on list. Deletion blocked if any employee
references the row.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import raise_conflict_if_referenced
from app.db import get_db
from app.models import Country, Employee, StateProvince
from app.schemas.lookups import (
    StateProvinceCreate,
    StateProvinceOut,
    StateProvinceUpdate,
)
from app.services.auth import Principal, get_authenticated_principal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/states-provinces", tags=["lookups"])


def _validate_country_exists(db: Session, country_id: int) -> None:
    if db.get(Country, country_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"country_id {country_id} does not exist.",
        )


@router.get("/", response_model=list[StateProvinceOut])
def list_states(
    country_id: int | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> list[StateProvince]:
    """List states/provinces. Filter by `country_id` to populate dependent dropdowns."""
    query = db.query(StateProvince)
    if country_id is not None:
        query = query.filter(StateProvince.country_id == country_id)
    if is_active is not None:
        query = query.filter(StateProvince.is_active == is_active)
    return query.order_by(StateProvince.name).all()


@router.get("/{state_id}", response_model=StateProvinceOut)
def get_state(
    state_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> StateProvince:
    state = db.get(StateProvince, state_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="State/province not found."
        )
    return state


@router.post("/", response_model=StateProvinceOut, status_code=status.HTTP_201_CREATED)
def create_state(
    body: StateProvinceCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> StateProvince:
    _validate_country_exists(db, body.country_id)
    state = StateProvince(
        country_id=body.country_id,
        code=body.code,
        name=body.name,
        is_active=body.is_active,
    )
    db.add(state)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A state/province with that name already exists for this country.",
        ) from None
    db.refresh(state)
    log.info(
        "state_created",
        extra={"state_id": state.id, "by": principal.identifier},
    )
    return state


@router.patch("/{state_id}", response_model=StateProvinceOut)
def update_state(
    state_id: int,
    body: StateProvinceUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> StateProvince:
    state = db.get(StateProvince, state_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="State/province not found."
        )
    data = body.model_dump(exclude_unset=True)
    if "country_id" in data:
        _validate_country_exists(db, data["country_id"])
    for field, value in data.items():
        setattr(state, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That state/province name is already in use for this country.",
        ) from None
    db.refresh(state)
    log.info(
        "state_updated",
        extra={"state_id": state.id, "by": principal.identifier},
    )
    return state


@router.delete("/{state_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_state(
    state_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> None:
    state = db.get(StateProvince, state_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="State/province not found."
        )
    raise_conflict_if_referenced(
        db=db,
        target_label=f"state/province '{state.name}'",
        references=[
            ("employees", Employee, Employee.state_province_id, state_id),
        ],
    )
    db.delete(state)
    db.commit()
    log.info(
        "state_deleted",
        extra={"state_id": state_id, "by": principal.identifier},
    )
