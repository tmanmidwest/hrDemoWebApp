"""Live connector-schema export.

Describes the employee attribute surface **as it exists in this instance** so a
connector builder (e.g. Saviynt) can map fields without guessing. The static
OpenAPI spec covers the API framework, but `custom_fields` appears there as a
generic object — this export enumerates the instance's *actual* custom fields
(from the registry), plus every core/reference attribute, enumerations, and a
masked sample record.

Two renderings:
* :func:`build_schema` — a structured dict (JSON export + API endpoint).
* :func:`schema_to_csv` — a flat attribute catalog (path, type, nullable, …).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy.orm import Session

from app import __version__
from app.models import Employee, EmploymentStatus
from app.schemas.employee import EmployeeOut
from app.services import custom_fields as cf

# Stable catalog of core + nested-reference attributes the employee API returns.
# (path, type, nullable, group, description). Custom fields and examples are
# layered on top at runtime.
_CORE_ATTRIBUTES: list[tuple[str, str, bool, str, str]] = [
    ("id", "integer", False, "core", "Internal numeric id (stable within this instance)."),
    ("employee_number", "string", False, "core", "Business key / employee number — the natural identifier for matching."),
    ("first_name", "string", False, "core", "Given name."),
    ("middle_name", "string", True, "core", "Middle name, if any."),
    ("last_name", "string", False, "core", "Family name."),
    ("date_of_birth", "string(date)", True, "core", "Date of birth (YYYY-MM-DD)."),
    ("ssn_masked", "string", True, "core", "SSN masked to the last four digits. The raw SSN is never exposed by the API."),
    ("address_line_1", "string", True, "address", "Street address line 1."),
    ("address_line_2", "string", True, "address", "Street address line 2."),
    ("city", "string", True, "address", "City."),
    ("postal_code", "string", True, "address", "Postal / ZIP code."),
    ("country.code", "string", False, "reference", "ISO country code of the employee's country."),
    ("country.name", "string", False, "reference", "Country name."),
    ("state_province.code", "string", True, "reference", "State/province code."),
    ("state_province.name", "string", True, "reference", "State/province name."),
    ("home_phone", "string", True, "contact", "Home phone number."),
    ("personal_email", "string", True, "contact", "Personal email address."),
    ("work_email", "string", True, "contact", "Work email address."),
    ("cost_center", "string", True, "employment", "Cost center."),
    ("employment_status.label", "string", True, "reference", "Employment status label (e.g. Active). Null if unset."),
    ("employment_status.value", "integer", True, "reference", "Stable numeric status code — prefer this for status mapping (see enumerations). Null if unset."),
    ("employment_status.is_active_status", "boolean", True, "reference", "Whether this status counts as active. Null if status unset."),
    ("department.name", "string", True, "reference", "Department name. Null if unassigned."),
    ("job_title.name", "string", True, "reference", "Job title name. Null if unassigned."),
    ("location.name", "string", True, "reference", "Assigned location name (optional)."),
    ("hire_date", "string(date)", True, "employment", "Hire date (YYYY-MM-DD). May be null."),
    ("termination_date", "string(date)", True, "employment", "Termination date (YYYY-MM-DD), if terminated."),
    ("supervisor.employee_number", "string", True, "reference", "Supervisor's employee number — use to build the manager hierarchy."),
    ("supervisor.first_name", "string", True, "reference", "Supervisor's first name."),
    ("supervisor.last_name", "string", True, "reference", "Supervisor's last name."),
    ("is_reference_manager", "boolean", False, "lifecycle", "True for static stand-in manager records (excluded from bulk reads)."),
    ("is_archived", "boolean", False, "lifecycle", "Soft-delete flag."),
    ("archived_at", "string(datetime)", True, "lifecycle", "When the record was archived, if archived."),
    ("created_at", "string(datetime)", False, "lifecycle", "Record creation timestamp."),
    ("updated_at", "string(datetime)", False, "lifecycle", "Record last-update timestamp."),
]

_CUSTOM_TYPE_MAP = {
    "text": "string",
    "number": "number",
    "boolean": "boolean",
    "date": "string(date)",
}


def _sample_employee(db: Session) -> Employee | None:
    """A representative real employee for example values (never a reference mgr)."""
    return (
        db.query(Employee)
        .filter(
            Employee.is_archived.is_(False),
            Employee.is_reference_manager.is_(False),
        )
        .order_by(Employee.employee_number)
        .first()
    )


def _dig(data: dict[str, Any], path: str) -> Any:
    """Walk a dotted path through a nested dict; return None if any hop misses."""
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _example_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_schema(db: Session) -> dict[str, Any]:
    """Build the structured connector schema for the live instance."""
    sample = _sample_employee(db)
    sample_dict: dict[str, Any] = (
        EmployeeOut.model_validate(sample).model_dump(mode="json") if sample else {}
    )

    definitions = cf.list_definitions(db, active_only=True)

    attributes: list[dict[str, Any]] = []
    for path, type_, nullable, group, description in _CORE_ATTRIBUTES:
        attributes.append(
            {
                "path": path,
                "type": type_,
                "nullable": nullable,
                "group": group,
                "description": description,
                "example": _example_str(_dig(sample_dict, path)),
            }
        )

    # Instance-specific custom fields — the part the static spec can't show.
    custom_values = sample_dict.get("custom_fields", {}) if sample_dict else {}
    for d in definitions:
        path = f"custom_fields.{d.key}"
        attributes.append(
            {
                "path": path,
                "type": _CUSTOM_TYPE_MAP.get(d.data_type, "string"),
                # Admin-managed: a custom field marked required in the registry is
                # enforced on write, so the connector contract says so too.
                "nullable": not d.is_required,
                "group": "custom",
                "description": d.description or f"Custom attribute “{d.label}”.",
                "example": _example_str(
                    custom_values.get(d.key) if isinstance(custom_values, dict) else None
                ),
            }
        )

    statuses = (
        db.query(EmploymentStatus).order_by(EmploymentStatus.value).all()
    )

    return {
        "instance": {
            "app_version": __version__,
            "employee_count": db.query(Employee)
            .filter(
                Employee.is_archived.is_(False),
                Employee.is_reference_manager.is_(False),
            )
            .count(),
            "custom_field_count": len(definitions),
            "note": (
                "Reflects this instance at generation time. Attribute paths are "
                "relative to an item in the employee API response."
            ),
        },
        "endpoints": {
            "list": "GET /api/v1/employees",
            "detail": "GET /api/v1/employees/{id}",
            "schema": "GET /api/v1/employees/schema",
            "auth": "Bearer API key, or OAuth2 client-credentials (scope: employees:read).",
        },
        "attributes": attributes,
        "enumerations": {
            "employment_status": [
                {
                    "label": s.label,
                    "value": s.value,
                    "is_active_status": s.is_active_status,
                }
                for s in statuses
            ],
        },
        "custom_fields": [
            {
                "key": d.key,
                "label": d.label,
                "data_type": d.data_type,
                "description": d.description,
                "required": d.is_required,
                "in_csv_export": d.include_in_export,
            }
            for d in definitions
        ],
        "sample_record": sample_dict or None,
    }


def schema_to_csv(schema: dict[str, Any]) -> str:
    """Flatten the attribute catalog to CSV (path, type, nullable, group, …)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["path", "type", "nullable", "group", "example", "description"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for attr in schema.get("attributes", []):
        row = dict(attr)
        row["nullable"] = "yes" if attr.get("nullable") else "no"
        writer.writerow(row)
    return buffer.getvalue()
