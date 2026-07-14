"""Backup export endpoint.

Generates a full-instance backup zip (database + secret keys) for download over
the REST API, mirroring the UI's Settings → Backup export. Restore is
intentionally not exposed over the API (it is destructive and UI-only).

Access requires a bearer token (API key or OAuth client).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services import backup as backup_service
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupRequest(BaseModel):
    """Optional body for POST /backup."""

    password: str | None = Field(default=None, max_length=255)


@router.post(
    "",
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "The backup archive. Encrypted when a password is supplied.",
        }
    },
)
def create_backup(
    request: Request,
    body: BackupRequest | None = None,
    principal: Principal = Depends(require_scope("backup:create")),
) -> Response:
    """Generate and return a full-instance backup zip.

    Supply ``{"password": "..."}`` to AES-256 encrypt the archive. The zip
    contains the database and this instance's secret keys — store it securely.
    """
    password = (body.password if body else None) or None
    try:
        data, filename = backup_service.create_backup(password)
    except backup_service.BackupError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup failed: {exc}",
        ) from exc

    log.info(
        "api_backup_created",
        extra={"encrypted": bool(password), "size": len(data), "by": principal.identifier},
    )
    record_event(
        category="system",
        event_type="system.backup.created",
        **principal_actor(principal),
        message="Generated a backup",
        detail={"surface": "api", "encrypted": bool(password), "size": len(data)},
        request=request,
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
