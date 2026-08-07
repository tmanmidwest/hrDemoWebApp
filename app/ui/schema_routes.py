"""Admin Connector Schema page — live attribute catalog + downloads.

Surfaces the same schema as ``GET /api/v1/employees/schema`` in the UI: a live
preview of every attribute the employee API exposes for this instance (including
its actual custom fields), with JSON and CSV downloads for handing to a connector
builder such as Saviynt.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser
from app.services import schema_export
from app.services.audit import record_event
from app.ui.dependencies import require_admin
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/admin/schema", tags=["ui"], include_in_schema=False)


@router.get("")
def show_schema(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    schema = schema_export.build_schema(db)
    return render(
        request,
        "schema/index.html",
        current_user=user,
        active_section="schema",
        schema=schema,
    )


@router.get("/export.json")
def export_json(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    schema = schema_export.build_schema(db)
    _audit(request, user, "json")
    return Response(
        content=json.dumps(schema, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="employee-connector-schema.json"'
        },
    )


@router.get("/export.csv")
def export_csv(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    schema = schema_export.build_schema(db)
    _audit(request, user, "csv")
    return Response(
        content=schema_export.schema_to_csv(schema),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="employee-attributes.csv"'
        },
    )


def _audit(request: Request, user: AppUser, fmt: str) -> None:
    record_event(
        category="employee",
        event_type="employee.schema_exported",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        message=f"Exported connector schema ({fmt.upper()})",
        detail={"surface": "ui", "format": fmt},
        request=request,
    )
