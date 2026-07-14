"""Country CRUD endpoints.

All endpoints require an authenticated principal (API key or OAuth JWT).
Deletion is blocked if any employee or state_province still references the country.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1._helpers import raise_conflict_if_referenced
from app.db import get_db
from app.models import Country, Employee, StateProvince
from app.schemas.lookups import CountryCreate, CountryOut, CountryUpdate
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/countries", tags=["lookups"])


@router.get("/", response_model=list[CountryOut])
def list_countries(
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("lookups:read")),
) -> list[Country]:
    """List all countries. Filter by `is_active` if provided."""
    query = db.query(Country)
    if is_active is not None:
        query = query.filter(Country.is_active == is_active)
    return query.order_by(Country.name).all()


@router.get("/{country_id}", response_model=CountryOut)
def get_country(
    country_id: int,
    db: Session = Depends(get_db),
    _principal: Principal = Depends(require_scope("lookups:read")),
) -> Country:
    country = db.get(Country, country_id)
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Country not found."
        )
    return country


@router.post("/", response_model=CountryOut, status_code=status.HTTP_201_CREATED)
def create_country(
    body: CountryCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> Country:
    # ISO codes are uppercase by convention
    country = Country(
        code=body.code.upper(),
        name=body.name,
        is_active=body.is_active,
    )
    db.add(country)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country with code '{country.code}' already exists.",
        ) from None
    db.refresh(country)
    log.info(
        "country_created",
        extra={"country_id": country.id, "code": country.code, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.country.created",
        **principal_actor(principal),
        target_type="country",
        target_id=country.id,
        target_label=country.name,
        message=f"Created country '{country.name}'",
        detail={"surface": "api", "code": country.code},
    )
    return country


@router.patch("/{country_id}", response_model=CountryOut)
def update_country(
    country_id: int,
    body: CountryUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> Country:
    country = db.get(Country, country_id)
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Country not found."
        )
    data = body.model_dump(exclude_unset=True)
    if "code" in data:
        data["code"] = data["code"].upper()
    for field, value in data.items():
        setattr(country, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That country code is already in use by another row.",
        ) from None
    db.refresh(country)
    log.info(
        "country_updated",
        extra={"country_id": country.id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.country.updated",
        **principal_actor(principal),
        target_type="country",
        target_id=country.id,
        target_label=country.name,
        message=f"Updated country '{country.name}'",
        detail={"surface": "api", "code": country.code},
    )
    return country


@router.delete("/{country_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_country(
    country_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("lookups:write")),
) -> None:
    country = db.get(Country, country_id)
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Country not found."
        )

    raise_conflict_if_referenced(
        db=db,
        target_label=f"country '{country.code}'",
        references=[
            ("employees", Employee, Employee.country_id, country_id),
            ("states/provinces", StateProvince, StateProvince.country_id, country_id),
        ],
    )

    country_name = country.name
    country_code = country.code
    db.delete(country)
    db.commit()
    log.info(
        "country_deleted",
        extra={"country_id": country_id, "by": principal.identifier},
    )
    record_event(
        category="lookup",
        event_type="lookup.country.deleted",
        **principal_actor(principal),
        target_type="country",
        target_id=country_id,
        target_label=country_name,
        message=f"Deleted country '{country_name}'",
        detail={"surface": "api", "code": country_code},
    )
