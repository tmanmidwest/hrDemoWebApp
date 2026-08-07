"""Admin Data Import wizard — Upload → Map → Resolve → Preview → Commit.

A self-service, in-browser flow for loading a customer's HR extract. State for a
run in progress lives in an :class:`~app.models.import_batch.ImportBatch` row,
carried between steps by id; a finished mapping can be saved as a reusable
:class:`~app.models.import_batch.ImportProfile`.

Everything here is admin-only and audited. The actual parsing/mapping/commit
logic lives in :mod:`app.services.data_import`; these handlers are thin glue
around it plus the wizard's step navigation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser, ImportBatch, ImportProfile
from app.services import data_import
from app.services.audit import record_event
from app.services.custom_fields import slugify_key
from app.ui.dependencies import require_admin
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ui/admin/import", tags=["ui"], include_in_schema=False
)

_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_batch(db: Session, batch_id: int) -> ImportBatch | None:
    return db.get(ImportBatch, batch_id)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _spec_to_option(spec: dict) -> str:
    """Map a stored column spec back to its <select> option value."""
    target = spec.get("target", data_import.TARGET_IGNORE)
    if target.startswith(data_import.CUSTOM_PREFIX):
        return f"{data_import.CUSTOM_PREFIX}{spec.get('data_type', 'text')}"
    return target


def _option_to_spec(source_col: str, option: str) -> dict:
    """Map a submitted <select> option value back to a stored column spec."""
    if option.startswith(data_import.CUSTOM_PREFIX):
        data_type = option[len(data_import.CUSTOM_PREFIX):] or "text"
        key = slugify_key(source_col) or "field"
        return {
            "target": f"{data_import.CUSTOM_PREFIX}{key}",
            "label": source_col,
            "data_type": data_type,
        }
    return {"target": option}


# ---------------------------------------------------------------------------
# Step 1 — landing / upload
# ---------------------------------------------------------------------------


@router.get("")
def show_landing(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    profiles = db.query(ImportProfile).order_by(ImportProfile.name).all()
    recent = (
        db.query(ImportBatch)
        .filter(ImportBatch.status == "committed")
        .order_by(ImportBatch.updated_at.desc())
        .limit(5)
        .all()
    )
    return render(
        request,
        "data_import/upload.html",
        current_user=user,
        active_section="data_import",
        profiles=profiles,
        recent=recent,
    )


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    raw = await file.read()
    if not raw:
        flash(request, "Please choose a file to upload.", "error")
        return _redirect("/ui/admin/import")
    if len(raw) > _MAX_UPLOAD_BYTES:
        flash(request, "That file is too large (limit 5 MB).", "error")
        return _redirect("/ui/admin/import")

    records, columns, err = data_import.parse_upload(file.filename or "", raw)
    if err:
        flash(request, err, "error")
        return _redirect("/ui/admin/import")
    if not records:
        flash(request, "That file has no data rows.", "error")
        return _redirect("/ui/admin/import")

    # Apply a saved profile if one was chosen and its shape matches; else suggest.
    form = await request.form()
    profile_id = form.get("profile_id")
    column_map: dict = {}
    value_map: dict = {}
    options = {"auto_create_lookups": True}
    applied_profile: ImportProfile | None = None
    if profile_id:
        applied_profile = db.get(ImportProfile, int(profile_id))
        if applied_profile is not None:
            column_map = {
                k: v for k, v in applied_profile.column_map.items() if k in columns
            }
            value_map = dict(applied_profile.value_map)
            options = dict(applied_profile.options) or options
    # Fill any unmapped columns with suggestions.
    suggested = data_import.suggest_mapping(columns)
    for col in columns:
        column_map.setdefault(col, suggested[col])
    if not value_map:
        value_map = data_import.suggest_value_map(db, records, column_map)

    batch = ImportBatch(
        filename=file.filename,
        status="uploaded",
        records=records,
        source_columns=columns,
        column_map=column_map,
        value_map=value_map,
        options=options,
        profile_id=applied_profile.id if applied_profile else None,
        created_by=user.username,
        row_count=len(records),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    log.info(
        "data_import_uploaded",
        extra={
            "batch_id": batch.id,
            "rows": len(records),
            "columns": len(columns),
            "by": user.username,
        },
    )
    return _redirect(f"/ui/admin/import/{batch.id}/map")


# ---------------------------------------------------------------------------
# Step 2 — map columns
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/map")
def show_map(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None:
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")

    sample = batch.records[0] if batch.records else {}
    columns_view = []
    for col in batch.source_columns:
        spec = batch.column_map.get(col, {"target": data_import.TARGET_IGNORE})
        columns_view.append(
            {
                "source": col,
                "sample": sample.get(col, ""),
                "selected": _spec_to_option(spec),
            }
        )
    auto_count = sum(
        1
        for c in columns_view
        if c["selected"] not in (data_import.TARGET_IGNORE, data_import.TARGET_SPLIT_NAME)
        and not c["selected"].startswith(data_import.CUSTOM_PREFIX)
    )
    custom_count = sum(
        1 for c in columns_view if c["selected"].startswith(data_import.CUSTOM_PREFIX)
    )
    return render(
        request,
        "data_import/map.html",
        current_user=user,
        active_section="data_import",
        batch=batch,
        columns_view=columns_view,
        standard_targets=data_import.STANDARD_TARGETS,
        auto_count=auto_count,
        custom_count=custom_count,
        step=2,
    )


@router.post("/{batch_id}/map")
async def save_map(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None:
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")

    form = await request.form()
    sources = form.getlist("source")
    targets = form.getlist("target")
    column_map: dict = {}
    for src, opt in zip(sources, targets, strict=False):
        column_map[str(src)] = _option_to_spec(str(src), str(opt))
    batch.column_map = column_map

    # Refresh value-map suggestions for any newly-mapped country/status columns,
    # keeping existing edits.
    suggested_vm = data_import.suggest_value_map(db, batch.records, column_map)
    merged_vm = dict(suggested_vm)
    merged_vm.update(batch.value_map or {})
    batch.value_map = merged_vm
    batch.status = "mapped"

    # Optionally save as a reusable profile.
    save_profile = form.get("save_profile")
    profile_name = (form.get("profile_name") or "").strip()
    if save_profile and profile_name:
        _save_profile(db, batch, profile_name, user)
        flash(request, f"Saved mapping profile “{profile_name}”.", "success")

    db.commit()
    return _redirect(f"/ui/admin/import/{batch.id}/resolve")


def _save_profile(
    db: Session, batch: ImportBatch, name: str, user: AppUser
) -> ImportProfile:
    existing = (
        db.query(ImportProfile).filter(ImportProfile.name == name).first()
    )
    if existing is not None:
        existing.source_columns = batch.source_columns
        existing.column_map = batch.column_map
        existing.value_map = batch.value_map
        existing.options = batch.options
        profile = existing
    else:
        profile = ImportProfile(
            name=name,
            source_columns=batch.source_columns,
            column_map=batch.column_map,
            value_map=batch.value_map,
            options=batch.options,
            created_by=user.username,
        )
        db.add(profile)
    db.flush()
    batch.profile_id = profile.id
    return profile


# ---------------------------------------------------------------------------
# Step 3 — resolve values & lookups
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/resolve")
def show_resolve(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None:
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")

    plan = data_import.analyze(
        db, batch.records, batch.column_map, batch.value_map
    )
    return render(
        request,
        "data_import/resolve.html",
        current_user=user,
        active_section="data_import",
        batch=batch,
        plan=plan,
        auto_create=bool(batch.options.get("auto_create_lookups", True)),
        step=3,
    )


@router.post("/{batch_id}/resolve")
async def save_resolve(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None:
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")

    form = await request.form()
    srcs = form.getlist("vm_src")
    vals = form.getlist("vm_val")
    mapped = form.getlist("vm_mapped")
    value_map: dict = {}
    for src, val, mp in zip(srcs, vals, mapped, strict=False):
        src, val, mp = str(src), str(val), str(mp).strip()
        if not mp or mp == val:
            continue
        value_map.setdefault(src, {})[val] = mp
    batch.value_map = value_map

    options = dict(batch.options or {})
    options["auto_create_lookups"] = form.get("auto_create_lookups") is not None
    batch.options = options
    batch.status = "resolved"
    db.commit()
    return _redirect(f"/ui/admin/import/{batch.id}/preview")


# ---------------------------------------------------------------------------
# Step 4 — preview
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/preview")
def show_preview(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None:
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")

    preview = data_import.preview(
        db, batch.records, batch.column_map, batch.value_map, batch.options
    )
    return render(
        request,
        "data_import/preview.html",
        current_user=user,
        active_section="data_import",
        batch=batch,
        preview=preview,
        step=4,
    )


# ---------------------------------------------------------------------------
# Commit + done
# ---------------------------------------------------------------------------


@router.post("/{batch_id}/commit")
def commit_batch(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None:
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")
    if batch.status == "committed":
        return _redirect(f"/ui/admin/import/{batch.id}/done")

    result, lookups = data_import.commit(
        db, batch.records, batch.column_map, batch.value_map, batch.options
    )

    batch.status = "committed"
    batch.result = {
        "created": len(result.created),
        "updated": len(result.updated),
        "skipped": result.skipped,
        "lookups": lookups,
    }
    db.commit()

    # Per-row audit events, mirroring the CSV import flow.
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
            detail={"surface": "data_import", "batch_id": batch.id},
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
            detail={"surface": "data_import", "batch_id": batch.id},
            request=request,
        )
    record_event(
        category="employee",
        event_type="employee.imported",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="employee",
        message=(
            f"Data Import: {len(result.created)} new, {len(result.updated)} "
            f"updated, {result.skipped} skipped"
        ),
        detail={
            "surface": "data_import",
            "batch_id": batch.id,
            "filename": batch.filename,
            "lookups": lookups,
        },
        request=request,
    )
    log.info(
        "data_import_committed",
        extra={
            "batch_id": batch.id,
            "n_created": len(result.created),
            "n_updated": len(result.updated),
            "n_skipped": result.skipped,
            "lookups": lookups,
            "by": user.username,
        },
    )
    flash(
        request,
        f"Import complete — {len(result.created)} added, "
        f"{len(result.updated)} updated.",
        "success",
    )
    return _redirect(f"/ui/admin/import/{batch.id}/done")


@router.get("/{batch_id}/done")
def show_done(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    batch = _get_batch(db, batch_id)
    if batch is None or batch.status != "committed":
        flash(request, "That import could not be found.", "error")
        return _redirect("/ui/admin/import")
    profile = db.get(ImportProfile, batch.profile_id) if batch.profile_id else None
    return render(
        request,
        "data_import/done.html",
        current_user=user,
        active_section="data_import",
        batch=batch,
        result=batch.result or {},
        profile=profile,
        step=5,
    )
