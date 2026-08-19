"""Admin User Governance page — the IGA lifecycle surface + downloads.

The counterpart to the employee Connector Schema page: instead of the employee
*data* surface, this documents everything an IGA platform (e.g. Saviynt) needs
to govern console **users** — create, update, disable/enable an account, read
the role catalog, and change a user's role — with JSON and CSV downloads to hand
to a connector builder.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser
from app.services import governance_schema
from app.services.audit import record_event
from app.ui.dependencies import require_admin
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/admin/governance", tags=["ui"], include_in_schema=False)


@router.get("")
def show_governance(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    schema = governance_schema.build_schema(db)
    return render(
        request,
        "governance/index.html",
        current_user=user,
        active_section="governance",
        schema=schema,
    )


@router.get("/export.json")
def export_json(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    schema = governance_schema.build_schema(db)
    _audit(request, user, "json")
    return Response(
        content=json.dumps(schema, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="user-governance-guide.json"'
        },
    )


@router.get("/export.csv")
def export_csv(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    schema = governance_schema.build_schema(db)
    _audit(request, user, "csv")
    return Response(
        content=governance_schema.schema_to_csv(schema),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="user-account-attributes.csv"'
        },
    )


def _audit(request: Request, user: AppUser, fmt: str) -> None:
    record_event(
        category="app_user",
        event_type="app_user.governance_exported",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="app_user",
        message=f"Exported user-governance guide ({fmt.upper()})",
        detail={"surface": "ui", "format": fmt},
        request=request,
    )
