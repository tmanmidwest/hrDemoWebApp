"""Location CRUD endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import raise_conflict_if_referenced
from app.db import get_db
from app.models import Employee, Location
from app.schemas.lookups import LocationCreate, LocationOut, LocationUpdate
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, get_authenticated_principal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/locations", tags=["lookups"])


@router.get("/", response_model=list[LocationOut])
def list_locations(
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> list[Location]:
    query = db.query(Location)
    if is_active is not None:
        query = query.filter(Location.is_active == is_active)
    return query.order_by(Location.name).all()


@router.get("/{location_id}", response_model=LocationOut)
def get_location(
    location_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(get_authenticated_principal),
) -> Location:
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found."
        )
    return loc


@router.post("/", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
def create_location(
    body: LocationCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Location:
    loc = Location(name=body.name, is_active=body.is_active)
    db.add(loc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Location '{body.name}' already exists.",
        ) from None
    db.refresh(loc)
    log.info(
        "location_created",
        extra={"location_id": loc.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.location.created",
        **principal_actor(principal),
        target_type="location",
        target_id=loc.id,
        target_label=loc.name,
        message=f"Created location '{loc.name}'",
        detail={"surface": "api"},
    )
    return loc


@router.patch("/{location_id}", response_model=LocationOut)
def update_location(
    location_id: int,
    body: LocationUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> Location:
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found."
        )
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(loc, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A location with that name already exists.",
        ) from None
    db.refresh(loc)
    log.info(
        "location_updated",
        extra={"location_id": loc.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.location.updated",
        **principal_actor(principal),
        target_type="location",
        target_id=loc.id,
        target_label=loc.name,
        message=f"Updated location '{loc.name}'",
        detail={"surface": "api"},
    )
    return loc


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(
    location_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_authenticated_principal),
) -> None:
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found."
        )
    raise_conflict_if_referenced(
        db=db,
        target_label=f"location '{loc.name}'",
        references=[
            ("employees", Employee, Employee.location_id, location_id),
        ],
    )
    location_name = loc.name
    db.delete(loc)
    db.commit()
    log.info(
        "location_deleted",
        extra={"location_id": location_id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.location.deleted",
        **principal_actor(principal),
        target_type="location",
        target_id=location_id,
        target_label=location_name,
        message=f"Deleted location '{location_name}'",
        detail={"surface": "api"},
    )
