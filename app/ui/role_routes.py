"""UI route for the read-only role catalog.

Admin-only. Roles are the fixed console-account authorization levels (View Only
/ Management / Admin), backed by the `UserRole` enum. This page mirrors the
entitlement catalog an IGA platform imports via ``GET /api/v1/roles`` — it is
read-only; a user's role is assigned on the Users page (Settings → Admin Users).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser, UserRole
from app.ui.dependencies import require_admin
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/roles", tags=["ui"], include_in_schema=False)


@router.get("")
def list_roles(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    """Read-only view of the role catalog and how many accounts hold each role."""
    rows = (
        db.query(AppUser.role, func.count(AppUser.id))
        .filter(AppUser.is_active)
        .group_by(AppUser.role)
        .all()
    )
    counts: dict[str, int] = {role: count for role, count in rows}
    roles = [
        {**entry, "assigned": counts.get(entry["id"], 0)}
        for entry in UserRole.catalog()
    ]
    return render(
        request,
        "roles/list.html",
        current_user=user,
        active_section="roles",
        roles=roles,
    )
