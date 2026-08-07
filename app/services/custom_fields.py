"""Custom field registry helpers.

Bridges the :class:`~app.models.custom_field.CustomFieldDefinition` registry and
the employee's ``custom_fields`` JSON bag: coercing raw strings (from a form or
a spreadsheet cell) into typed values, validating them, and producing a clean,
ordered view for the API/UI.

Values are stored in the bag as native JSON types — ``str`` for text, ``float``/
``int`` for number, ``bool`` for boolean, and an ISO ``YYYY-MM-DD`` ``str`` for
date (JSON has no date type; we keep the round-trippable string).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import CustomFieldDefinition
from app.models.custom_field import DATA_TYPES

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TRUEY = {"true", "yes", "y", "1", "t"}
_FALSEY = {"false", "no", "n", "0", "f", ""}


class CustomFieldError(ValueError):
    """A value could not be coerced/validated for its field type."""


def slugify_key(raw: str) -> str:
    """Turn an arbitrary label into a valid custom-field key.

    "Org Relation" -> "org_relation"; "Full/Part" -> "full_part".
    """
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s:
        return s
    if s[0].isdigit():
        s = f"f_{s}"
    return s


def valid_key(key: str) -> bool:
    return bool(_KEY_RE.match(key))


def list_definitions(
    db: Session, *, active_only: bool = True
) -> list[CustomFieldDefinition]:
    """Return custom field definitions in display order."""
    q = db.query(CustomFieldDefinition)
    if active_only:
        q = q.filter(CustomFieldDefinition.is_active.is_(True))
    return q.order_by(
        CustomFieldDefinition.display_order, CustomFieldDefinition.key
    ).all()


def definitions_by_key(
    db: Session, *, active_only: bool = True
) -> dict[str, CustomFieldDefinition]:
    return {d.key: d for d in list_definitions(db, active_only=active_only)}


def coerce_value(data_type: str, raw: object) -> object | None:
    """Coerce a raw value to the field's type. Returns None for blank input.

    Raises :class:`CustomFieldError` if the value is present but invalid.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None

    if data_type == "text":
        return s
    if data_type == "number":
        try:
            f = float(s)
        except ValueError as exc:
            raise CustomFieldError(f"'{s}' is not a number.") from exc
        return int(f) if f.is_integer() else f
    if data_type == "boolean":
        low = s.lower()
        if low in _TRUEY:
            return True
        if low in _FALSEY:
            return False
        raise CustomFieldError(f"'{s}' is not a yes/no value.")
    if data_type == "date":
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        raise CustomFieldError(f"'{s}' is not a valid date (use YYYY-MM-DD).")
    # Unknown type: store as text rather than lose data.
    return s


@dataclass
class ResolvedCustomFields:
    """Outcome of resolving a set of raw custom-field cells against the registry."""

    values: dict[str, object]  # key -> coerced value (blank keys omitted)
    errors: list[str]


def resolve_custom_fields(
    db: Session, raw: dict[str, object]
) -> ResolvedCustomFields:
    """Coerce a {key: raw_value} mapping against active definitions.

    Unknown keys are ignored (they have no definition). Blank values are dropped
    so they don't clutter the bag or overwrite with empties.
    """
    defs = definitions_by_key(db)
    values: dict[str, object] = {}
    errors: list[str] = []
    for key, rawval in raw.items():
        definition = defs.get(key)
        if definition is None:
            continue
        try:
            coerced = coerce_value(definition.data_type, rawval)
        except CustomFieldError as exc:
            errors.append(f"{definition.label}: {exc}")
            continue
        if coerced is not None:
            values[key] = coerced
    return ResolvedCustomFields(values=values, errors=errors)


def ensure_definition(
    db: Session,
    *,
    key: str,
    label: str | None = None,
    data_type: str = "text",
    display_order: int | None = None,
) -> CustomFieldDefinition:
    """Get an existing definition by key, or create it. Does not commit."""
    if not valid_key(key):
        raise CustomFieldError(
            f"Custom field key '{key}' is invalid (use lowercase letters, "
            "numbers, and underscores; must start with a letter)."
        )
    if data_type not in DATA_TYPES:
        raise CustomFieldError(f"Unknown custom field type '{data_type}'.")

    existing = (
        db.query(CustomFieldDefinition)
        .filter(CustomFieldDefinition.key == key)
        .first()
    )
    if existing is not None:
        return existing

    if display_order is None:
        current_max = (
            db.query(CustomFieldDefinition.display_order)
            .order_by(CustomFieldDefinition.display_order.desc())
            .first()
        )
        display_order = ((current_max[0] if current_max else 0) or 0) + 10

    definition = CustomFieldDefinition(
        key=key,
        label=label or key.replace("_", " ").title(),
        data_type=data_type,
        display_order=display_order,
    )
    db.add(definition)
    db.flush()
    return definition


def display_value(data_type: str, value: object) -> str:
    """Human-readable rendering of a stored value for the UI."""
    if value is None:
        return ""
    if data_type == "boolean":
        return "Yes" if value else "No"
    if data_type == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value).strftime("%b %d, %Y")
        except ValueError:
            return value
    return str(value)
