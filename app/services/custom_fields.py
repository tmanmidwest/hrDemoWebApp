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

# Keys a custom field may not use, because a CSV column of that name already
# means something to the employee importer. Listed literally rather than
# imported from app.services.employee_import to keep that module free to depend
# on this one (custom columns in the export).
RESERVED_KEYS = frozenset(
    {
        "employee_number",
        "first_name",
        "middle_name",
        "last_name",
        "date_of_birth",
        "ssn",
        "address_line_1",
        "address_line_2",
        "city",
        "state_province",
        "postal_code",
        "country",
        "home_phone",
        "personal_email",
        "work_email",
        "cost_center",
        "employment_status",
        "department",
        "job_title",
        "location",
        "hire_date",
        "termination_date",
        "supervisor_employee_number",
        # Also reserved: the bag itself and the id, so an attribute path never
        # collides in the connector schema.
        "id",
        "custom_fields",
    }
)


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


def safe_key(raw: str, *, fallback: str = "field") -> str:
    """Slugify ``raw`` into a key that :func:`check_key` will accept.

    Used where a key is derived from something outside our control (a spreadsheet
    header): a blank slug becomes ``fallback``, and one that collides with a
    standard employee field is suffixed rather than rejected.
    """
    key = slugify_key(raw) or fallback
    if key in RESERVED_KEYS:
        key = f"{key}_custom"
    return key


def valid_key(key: str) -> bool:
    return bool(_KEY_RE.match(key))


def check_key(key: str) -> None:
    """Raise :class:`CustomFieldError` unless ``key`` is a usable field key."""
    if not valid_key(key):
        raise CustomFieldError(
            f"Key '{key}' is invalid — use lowercase letters, numbers and "
            "underscores, starting with a letter."
        )
    if key in RESERVED_KEYS:
        raise CustomFieldError(
            f"Key '{key}' is reserved by a standard employee field. "
            "Pick a different label or key."
        )


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


def next_display_order(db: Session) -> int:
    """Display order that places a new field after every existing one."""
    current_max = (
        db.query(CustomFieldDefinition.display_order)
        .order_by(CustomFieldDefinition.display_order.desc())
        .first()
    )
    return ((current_max[0] if current_max else 0) or 0) + 10


def ensure_definition(
    db: Session,
    *,
    key: str,
    label: str | None = None,
    data_type: str = "text",
    display_order: int | None = None,
    description: str | None = None,
    is_required: bool = False,
    include_in_export: bool = True,
) -> CustomFieldDefinition:
    """Get an existing definition by key, or create it. Does not commit.

    An existing definition is returned untouched — callers that mean to change a
    field's shape should edit it explicitly rather than relying on this.
    """
    check_key(key)
    if data_type not in DATA_TYPES:
        raise CustomFieldError(f"Unknown custom field type '{data_type}'.")

    existing = (
        db.query(CustomFieldDefinition)
        .filter(CustomFieldDefinition.key == key)
        .first()
    )
    if existing is not None:
        return existing

    definition = CustomFieldDefinition(
        key=key,
        label=label or key.replace("_", " ").title(),
        data_type=data_type,
        display_order=(
            next_display_order(db) if display_order is None else display_order
        ),
        description=description or None,
        is_required=is_required,
        include_in_export=include_in_export,
    )
    db.add(definition)
    db.flush()
    return definition


def required_definitions(db: Session) -> list[CustomFieldDefinition]:
    """Active definitions that must carry a value on every employee."""
    return [d for d in list_definitions(db, active_only=True) if d.is_required]


def missing_required(db: Session, bag: dict[str, object] | None) -> list[str]:
    """Labels of required custom fields absent (or blank) in ``bag``.

    ``bag`` is an employee's resulting ``custom_fields`` value — validate the
    *outcome* of a create/update, not just the submitted keys, so a record can
    never be saved in a state that violates the registry.
    """
    values = bag or {}
    missing: list[str] = []
    for d in required_definitions(db):
        value = values.get(d.key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(d.label)
    return missing


def export_definitions(db: Session) -> list[CustomFieldDefinition]:
    """Active definitions that round-trip through the employee CSV."""
    return [d for d in list_definitions(db, active_only=True) if d.include_in_export]


def _has_value(bag: dict[str, object] | None, key: str) -> bool:
    value = (bag or {}).get(key)
    if value is None:
        return False
    return not (isinstance(value, str) and value.strip() == "")


def _roster_bags(db: Session) -> list[dict[str, object] | None]:
    """``custom_fields`` bags for the working roster.

    Archived records and reference managers are excluded, matching the API
    roster and the CSV export.
    """
    from app.models import Employee

    rows = (
        db.query(Employee.custom_fields)
        .filter(
            Employee.is_archived.is_(False),
            Employee.is_reference_manager.is_(False),
        )
        .all()
    )
    return [bag for (bag,) in rows]


def employees_missing_value(db: Session, key: str) -> int:
    """How many roster employees have no value for ``key``.

    Used to warn an admin before they mark a field required: those records would
    then fail their next update until the value is supplied.
    """
    return sum(1 for bag in _roster_bags(db) if not _has_value(bag, key))


@dataclass
class UsageCounts:
    """How widely each custom field is populated across the roster."""

    roster_total: int
    filled: dict[str, int]  # key -> employees carrying a value


def usage_counts(db: Session, keys: list[str]) -> UsageCounts:
    """Count, in one pass, how many roster employees have a value per key."""
    bags = _roster_bags(db)
    return UsageCounts(
        roster_total=len(bags),
        filled={k: sum(1 for bag in bags if _has_value(bag, k)) for k in keys},
    )


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
