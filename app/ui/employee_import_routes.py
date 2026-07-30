"""HTML UI for bulk employee CSV import / export.

Flow:

* ``GET  /ui/employees/import/template.csv`` — blank template to fill out.
* ``GET  /ui/employees/export.csv``          — current roster in the same shape.
* ``GET  /ui/employees/import``              — upload page.
* ``POST /ui/employees/import/preview``      — dry-run: classify New/Update/Error.
* ``POST /ui/employees/import/commit``       — re-parse (stateless) and apply.

Everything is gated behind :func:`require_employee_manager`, and every
data-changing or data-exporting action records an audit event so it shows up in
the Activity Log.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser
from app.services import employee_import
from app.services.audit import record_event
from app.ui.dependencies import require_employee_manager
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/employees", tags=["ui"], include_in_schema=False)

# Guard against absurd uploads (this is a demo app, not a bulk ETL pipeline).
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def _csv_response(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Downloads: template + export
# ---------------------------------------------------------------------------


@router.get("/import/template.csv")
def download_template(
    request: Request,
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    record_event(
        category="employee",
        event_type="employee.import_template_downloaded",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        message="Downloaded the employee import template",
        detail={"surface": "ui"},
        request=request,
    )
    return _csv_response(
        employee_import.build_template_csv(), "employee-import-template.csv"
    )


@router.get("/export.csv")
def export_employees(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    csv_text = employee_import.export_employees_csv(db)
    record_event(
        category="employee",
        event_type="employee.exported",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        message="Exported employees to CSV",
        detail={"surface": "ui"},
        request=request,
    )
    return _csv_response(csv_text, "employees.csv")


# ---------------------------------------------------------------------------
# Upload page
# ---------------------------------------------------------------------------


@router.get("/import")
def show_import(
    request: Request,
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    return render(
        request,
        "employees/import.html",
        current_user=user,
        active_section="employees",
        preview=None,
        csv_text=None,
        columns=employee_import.COLUMNS,
        required_columns=employee_import.REQUIRED_FOR_NEW,
    )


def _decode_upload(raw: bytes) -> tuple[str | None, str | None]:
    """Decode uploaded bytes to text. Returns (text, error)."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return None, "Could not read the file — please upload a UTF-8 CSV."


# ---------------------------------------------------------------------------
# Preview (dry-run)
# ---------------------------------------------------------------------------


@router.post("/import/preview")
async def import_preview(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    raw = await file.read()
    if not raw:
        flash(request, "Please choose a CSV file to upload.", "error")
        return RedirectResponse(url="/ui/employees/import", status_code=303)
    if len(raw) > _MAX_UPLOAD_BYTES:
        flash(request, "That file is too large (limit 5 MB).", "error")
        return RedirectResponse(url="/ui/employees/import", status_code=303)

    csv_text, decode_err = _decode_upload(raw)
    if csv_text is None:
        flash(request, decode_err or "Could not read the file.", "error")
        return RedirectResponse(url="/ui/employees/import", status_code=303)

    preview = employee_import.parse_and_classify(db, csv_text)

    return render(
        request,
        "employees/import.html",
        current_user=user,
        active_section="employees",
        preview=preview,
        csv_text=csv_text,
        filename=file.filename,
        columns=employee_import.COLUMNS,
        required_columns=employee_import.REQUIRED_FOR_NEW,
    )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


@router.post("/import/commit")
async def import_commit(
    request: Request,
    csv_text: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_employee_manager),
) -> Response:
    # Re-parse and re-validate against current DB state (stateless).
    preview = employee_import.parse_and_classify(db, csv_text)
    if preview.parse_error:
        flash(request, preview.parse_error, "error")
        return RedirectResponse(url="/ui/employees/import", status_code=303)

    if not preview.committable:
        flash(request, "Nothing to import — every row had an error.", "warning")
        return RedirectResponse(url="/ui/employees/import", status_code=303)

    result = employee_import.commit_preview(db, preview)

    # Per-row audit events (so each shows up under the employee's activity),
    # mirroring the single-record create/update events.
    for applied in result.created:
        record_event(
            category="employee",
            event_type="employee.created",
            actor_type="user",
            actor_label=user.username,
            actor_id=user.id,
            target_type="employee",
            target_id=applied.employee_id,
            target_label=f"{applied.label} ({applied.employee_number})",
            message=f"Created employee {applied.label} ({applied.employee_number})",
            detail={"surface": "import", "employee_number": applied.employee_number},
            request=request,
        )
    for applied in result.updated:
        record_event(
            category="employee",
            event_type="employee.updated",
            actor_type="user",
            actor_label=user.username,
            actor_id=user.id,
            target_type="employee",
            target_id=applied.employee_id,
            target_label=f"{applied.label} ({applied.employee_number})",
            message=f"Updated employee {applied.label} ({applied.employee_number})",
            detail={"surface": "import", "employee_number": applied.employee_number},
            request=request,
        )

    # One summary event for the batch.
    record_event(
        category="employee",
        event_type="employee.imported",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        message=(
            f"Imported employees: {len(result.created)} new, "
            f"{len(result.updated)} updated, {result.skipped} skipped"
        ),
        detail={
            "surface": "ui",
            "created": len(result.created),
            "updated": len(result.updated),
            "skipped": result.skipped,
        },
        request=request,
    )

    log.info(
        "ui_employees_imported",
        extra={
            "by": user.username,
            "n_created": len(result.created),
            "n_updated": len(result.updated),
            "n_skipped": result.skipped,
        },
    )

    parts = []
    if result.created:
        parts.append(f"{len(result.created)} added")
    if result.updated:
        parts.append(f"{len(result.updated)} updated")
    if result.skipped:
        parts.append(f"{result.skipped} skipped")
    flash(request, "Import complete — " + ", ".join(parts) + ".", "success")
    return RedirectResponse(url="/ui/employees", status_code=303)
