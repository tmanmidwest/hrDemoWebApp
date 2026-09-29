"""Admin UI for the custom field registry.

The import wizard can *create* custom field definitions implicitly (a mapped
spreadsheet column becomes a field). This module is the explicit counterpart:
define a field by hand, give it a type, mark it required, reorder it, retire it,
or delete it.

Definitions are schema, not data. Two rules follow from that:

* A field's ``key`` is immutable after creation — it's the key inside every
  employee's ``custom_fields`` bag, the CSV column header, and the attribute path
  an external connector (e.g. Saviynt) has already been wired to.
* Marking a field required is checked against the existing roster first. Records
  that predate the field would otherwise fail their next update, so the admin is
  shown the count and has to confirm.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser, CustomFieldDefinition
from app.models.custom_field import DATA_TYPES
from app.services import custom_fields as cf
from app.services.audit import record_event
from app.ui.dependencies import require_admin
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ui/admin/custom-fields", tags=["ui"], include_in_schema=False
)

# Human labels for the type picker, in the order they're offered.
TYPE_LABELS: list[tuple[str, str]] = [
    ("text", "Text"),
    ("number", "Number"),
    ("boolean", "Yes / No"),
    ("date", "Date"),
]


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
def list_fields(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    definitions = cf.list_definitions(db, active_only=False)
    usage = cf.usage_counts(db, [d.key for d in definitions])
    rows = [
        {
            "id": d.id,
            "key": d.key,
            "label": d.label,
            "type_label": dict(TYPE_LABELS).get(d.data_type, d.data_type),
            "description": d.description or "",
            "display_order": d.display_order,
            "is_required": d.is_required,
            "is_active": d.is_active,
            "include_in_export": d.include_in_export,
            "filled": usage.filled.get(d.key, 0),
        }
        for d in definitions
    ]
    return render(
        request,
        "custom_fields/list.html",
        current_user=user,
        active_section="custom_fields",
        rows=rows,
        roster_total=usage.roster_total,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def _blank_form(db: Session) -> dict[str, object]:
    return {
        "label": "",
        "key": "",
        "data_type": "text",
        "description": "",
        "display_order": cf.next_display_order(db),
        "is_required": False,
        "is_active": True,
        "include_in_export": True,
    }


@router.get("/new")
def show_new_field(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "custom_fields/form.html",
        current_user=user,
        active_section="custom_fields",
        row=None,
        form=_blank_form(db),
        type_labels=TYPE_LABELS,
        form_action="/ui/admin/custom-fields/new",
    )


@router.post("/new")
def create_field(
    request: Request,
    label: str = Form(...),
    key: str = Form(""),
    data_type: str = Form("text"),
    description: str = Form(""),
    display_order: str = Form(""),
    is_required: str | None = Form(None),
    is_active: str | None = Form(None),
    include_in_export: str | None = Form(None),
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    form = {
        "label": label.strip(),
        # Blank key → derive one from the label, which is what most admins want.
        "key": cf.slugify_key(key.strip() or label),
        "data_type": data_type,
        "description": description.strip(),
        "display_order": _parse_order(display_order, cf.next_display_order(db)),
        "is_required": bool(is_required),
        "is_active": bool(is_active),
        "include_in_export": bool(include_in_export),
    }

    error = _validate(db, form, row=None)
    if error:
        return _form_response(request, user, db, None, form, error)

    # A required field the roster can't satisfy yet — show the blast radius and
    # make the admin confirm before it starts blocking updates.
    if form["is_required"] and not confirm:
        missing = cf.employees_missing_value(db, str(form["key"]))
        if missing:
            return _confirm_response(request, user, db, None, form, missing)

    definition = CustomFieldDefinition(
        key=form["key"],
        label=form["label"],
        data_type=form["data_type"],
        description=form["description"] or None,
        display_order=form["display_order"],
        is_required=form["is_required"],
        is_active=form["is_active"],
        include_in_export=form["include_in_export"],
    )
    db.add(definition)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _form_response(
            request,
            user,
            db,
            None,
            form,
            f"A custom field with key '{form['key']}' already exists.",
        )

    flash(request, f"Added custom field “{definition.label}”.", "success")
    _audit(request, user, "created", definition)
    return RedirectResponse(url="/ui/admin/custom-fields", status_code=303)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


@router.get("/{field_id}/edit")
def show_edit_field(
    field_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    d = _get_or_404(db, field_id)
    return render(
        request,
        "custom_fields/form.html",
        current_user=user,
        active_section="custom_fields",
        row=d,
        form={
            "label": d.label,
            "key": d.key,
            "data_type": d.data_type,
            "description": d.description or "",
            "display_order": d.display_order,
            "is_required": d.is_required,
            "is_active": d.is_active,
            "include_in_export": d.include_in_export,
        },
        type_labels=TYPE_LABELS,
        form_action=f"/ui/admin/custom-fields/{d.id}/edit",
        filled=cf.usage_counts(db, [d.key]).filled.get(d.key, 0),
    )


@router.post("/{field_id}/edit")
def update_field(
    field_id: int,
    request: Request,
    label: str = Form(...),
    data_type: str = Form("text"),
    description: str = Form(""),
    display_order: str = Form(""),
    is_required: str | None = Form(None),
    is_active: str | None = Form(None),
    include_in_export: str | None = Form(None),
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    d = _get_or_404(db, field_id)
    form = {
        # key is deliberately not editable — see the module docstring.
        "label": label.strip(),
        "key": d.key,
        "data_type": data_type,
        "description": description.strip(),
        "display_order": _parse_order(display_order, d.display_order),
        "is_required": bool(is_required),
        "is_active": bool(is_active),
        "include_in_export": bool(include_in_export),
    }

    error = _validate(db, form, row=d)
    if error:
        return _form_response(request, user, db, d, form, error)

    newly_required = form["is_required"] and not d.is_required
    if newly_required and not confirm:
        missing = cf.employees_missing_value(db, d.key)
        if missing:
            return _confirm_response(request, user, db, d, form, missing)

    before = {
        "label": d.label,
        "data_type": d.data_type,
        "description": d.description or "",
        "display_order": d.display_order,
        "is_required": d.is_required,
        "is_active": d.is_active,
        "include_in_export": d.include_in_export,
    }
    changed = [name for name, old in before.items() if old != form[name]]

    d.label = str(form["label"])
    d.data_type = str(form["data_type"])
    d.description = str(form["description"]) or None
    d.display_order = int(form["display_order"])  # type: ignore[arg-type]
    d.is_required = bool(form["is_required"])
    d.is_active = bool(form["is_active"])
    d.include_in_export = bool(form["include_in_export"])
    db.commit()

    flash(request, f"Updated “{d.label}”.", "success")
    _audit(request, user, "updated", d, extra={"changed": changed})
    return RedirectResponse(url="/ui/admin/custom-fields", status_code=303)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.post("/{field_id}/delete")
def delete_field(
    field_id: int,
    request: Request,
    confirm: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    d = _get_or_404(db, field_id)
    filled = cf.usage_counts(db, [d.key]).filled.get(d.key, 0)

    # Deleting the definition does not touch the values already sitting in each
    # employee's JSON bag — they'd simply stop being displayed or exported. Say
    # so plainly and offer "deactivate" as the reversible option.
    if filled and not confirm:
        return render(
            request,
            "custom_fields/delete_confirm.html",
            current_user=user,
            active_section="custom_fields",
            row=d,
            filled=filled,
        )

    key, label, field_pk = d.key, d.label, d.id
    db.delete(d)
    db.commit()
    flash(request, f"Deleted custom field “{label}”.", "success")
    record_event(
        category="lookup",
        event_type="lookup.custom_field.deleted",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="custom_field",
        target_id=field_pk,
        target_label=key,
        message=f"Deleted custom field '{key}' ({label})",
        detail={"surface": "ui", "employees_with_value": filled},
        request=request,
    )
    return RedirectResponse(url="/ui/admin/custom-fields", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_404(db: Session, field_id: int) -> CustomFieldDefinition:
    d = db.get(CustomFieldDefinition, field_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Custom field not found.")
    return d


def _parse_order(raw: str, fallback: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


def _validate(
    db: Session, form: dict[str, object], *, row: CustomFieldDefinition | None
) -> str | None:
    """Return a human-readable error, or None if the form is usable."""
    if not form["label"]:
        return "Label is required."
    if form["data_type"] not in DATA_TYPES:
        return f"'{form['data_type']}' is not a supported field type."
    if row is None:
        if not form["key"]:
            return (
                "Could not derive a key from that label — enter a key "
                "explicitly (lowercase letters, numbers, underscores)."
            )
        try:
            cf.check_key(str(form["key"]))
        except cf.CustomFieldError as exc:
            return str(exc)
    elif form["data_type"] != row.data_type:
        # Stored values are already coerced to the old type; re-typing a field
        # that holds data would leave the bag inconsistent with the registry.
        filled = cf.usage_counts(db, [row.key]).filled.get(row.key, 0)
        if filled:
            return (
                f"Can't change the type of “{row.label}” — {filled} employee(s) "
                "already have a value. Clear those values (or create a new "
                "field) first."
            )
    return None


def _form_response(
    request: Request,
    user: AppUser,
    db: Session,
    row: CustomFieldDefinition | None,
    form: dict[str, object],
    error: str,
) -> Response:
    return render(
        request,
        "custom_fields/form.html",
        current_user=user,
        active_section="custom_fields",
        row=row,
        form=form,
        type_labels=TYPE_LABELS,
        form_action=(
            f"/ui/admin/custom-fields/{row.id}/edit"
            if row is not None
            else "/ui/admin/custom-fields/new"
        ),
        filled=(
            cf.usage_counts(db, [row.key]).filled.get(row.key, 0)
            if row is not None
            else 0
        ),
        error=error,
    )


def _confirm_response(
    request: Request,
    user: AppUser,
    db: Session,
    row: CustomFieldDefinition | None,
    form: dict[str, object],
    missing: int,
) -> Response:
    """Re-post the whole form with confirm=1 after warning about the roster."""
    return render(
        request,
        "custom_fields/required_confirm.html",
        current_user=user,
        active_section="custom_fields",
        row=row,
        form=form,
        missing=missing,
        roster_total=cf.usage_counts(db, []).roster_total,
        form_action=(
            f"/ui/admin/custom-fields/{row.id}/edit"
            if row is not None
            else "/ui/admin/custom-fields/new"
        ),
    )


def _audit(
    request: Request,
    user: AppUser,
    verb: str,
    definition: CustomFieldDefinition,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    detail: dict[str, object] = {
        "surface": "ui",
        "key": definition.key,
        "data_type": definition.data_type,
        "is_required": definition.is_required,
        "is_active": definition.is_active,
        "include_in_export": definition.include_in_export,
    }
    if extra:
        detail.update(extra)
    record_event(
        category="lookup",
        event_type=f"lookup.custom_field.{verb}",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="custom_field",
        target_id=definition.id,
        target_label=definition.key,
        message=f"{verb.capitalize()} custom field '{definition.key}' ({definition.label})",
        detail=detail,
        request=request,
    )
