"""Pydantic schemas for the Employee API.

Responses include nested lookup objects (country, department, etc.) so IGA
systems get all the data they need in one call. Writes accept only FK IDs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

def _normalize_ssn_field(v: str | None) -> str | None:
    """Shared SSN normalizer for write schemas.

    Strips separators to bare digits and requires exactly 9. Blank input becomes
    None (SSN is optional). Kept in sync with
    app.services.employee_validation.normalize_ssn / validate_ssn_format.
    """
    if v is None:
        return None
    digits = "".join(ch for ch in v if ch.isdigit())
    if not digits:
        return None
    if len(digits) != 9:
        raise ValueError("ssn must be exactly 9 digits (numbers only).")
    return digits


# ---------------------------------------------------------------------------
# Nested reference shapes used in employee responses
# ---------------------------------------------------------------------------


class EmployeeCountryRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str


class EmployeeStateRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str | None
    name: str


class EmployeeStatusRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    value: int
    is_active_status: bool


class EmployeeDepartmentRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class EmployeeJobTitleRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class EmployeeLocationRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class EmployeeSupervisorRef(BaseModel):
    """Minimal employee reference for the supervisor field."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_number: str
    first_name: str
    last_name: str
    # True when this supervisor is a static reference manager (e.g.
    # "margaretmanager") rather than a real, syncable employee.
    is_reference_manager: bool = False


# ---------------------------------------------------------------------------
# Main employee shapes
# ---------------------------------------------------------------------------


class EmployeeOut(BaseModel):
    """Full employee record as returned by GET endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_number: str

    # Identity
    first_name: str
    middle_name: str | None
    last_name: str

    # Personal. SSN is exposed masked only (last 4) — the raw value is never
    # returned by the API.
    date_of_birth: date | None
    ssn_masked: str | None

    # Address
    address_line_1: str | None
    address_line_2: str | None
    city: str | None
    country: EmployeeCountryRef
    state_province: EmployeeStateRef | None
    postal_code: str | None

    # Contact
    home_phone: str | None
    personal_email: str | None
    work_email: str | None

    # Employment
    cost_center: str | None
    employment_status: EmployeeStatusRef
    department: EmployeeDepartmentRef
    job_title: EmployeeJobTitleRef
    location: EmployeeLocationRef | None
    hire_date: date | None
    termination_date: date | None
    supervisor: EmployeeSupervisorRef | None

    # Admin-defined custom attributes (see CustomFieldDefinition). Keyed by the
    # field's stable slug. Present so downstream systems (e.g. Saviynt) get every
    # attribute in one call.
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    # Static reference manager: a stand-in supervisor record that is hidden from
    # the roster list, CSV export, and reports, but still assignable and
    # fetchable. See app.services.reference_managers.
    is_reference_manager: bool

    # Lifecycle
    is_archived: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Create / Update payloads
# ---------------------------------------------------------------------------


class EmployeeCreate(BaseModel):
    """All required fields per the spec, plus optional ones."""

    # Required
    employee_number: str = Field(min_length=1, max_length=50)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    country_id: int
    employment_status_id: int
    department_id: int
    job_title_id: int

    # Optional
    hire_date: date | None = None
    middle_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date | None = None
    ssn: str | None = Field(
        default=None,
        description="Social Security Number, 9 digits. Separators are stripped.",
    )
    address_line_1: str | None = Field(default=None, max_length=200)
    address_line_2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state_province_id: int | None = None
    postal_code: str | None = Field(default=None, max_length=20)
    home_phone: str | None = Field(default=None, max_length=50)
    personal_email: str | None = Field(default=None, max_length=255)
    work_email: str | None = Field(default=None, max_length=255)
    cost_center: str | None = Field(default=None, max_length=100)
    termination_date: date | None = None
    supervisor_id: int | None = None  # Required only if other employees exist
    location_id: int | None = None  # Optional — location is not required
    # Admin-defined custom attributes, keyed by CustomFieldDefinition.key. Values
    # are coerced/validated against the registry in the route.
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ssn")
    @classmethod
    def _validate_ssn(cls, v: str | None) -> str | None:
        return _normalize_ssn_field(v)

    @model_validator(mode="after")
    def _validate_dates(self) -> EmployeeCreate:
        if (
            self.termination_date is not None
            and self.hire_date is not None
            and self.termination_date < self.hire_date
        ):
            raise ValueError("termination_date must be on or after hire_date")
        if self.date_of_birth is not None and self.date_of_birth > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return self


class EmployeeUpdate(BaseModel):
    """PATCH payload — every field optional.

    Status can be set via either `employment_status_id` (DB primary key) or
    `employment_status_value` (the stable IGA-facing numeric code, e.g. 1=Active,
    0=Not Active, 3=Terminated). The `_value` form is preferred for IGA writes
    because PKs can shift across deployments while values are stable.
    Sending both in the same request is a 400.
    """

    employee_number: str | None = Field(default=None, min_length=1, max_length=50)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: date | None = None
    ssn: str | None = Field(
        default=None,
        description="Social Security Number, 9 digits. Separators are stripped.",
    )

    address_line_1: str | None = Field(default=None, max_length=200)
    address_line_2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country_id: int | None = None
    state_province_id: int | None = None
    postal_code: str | None = Field(default=None, max_length=20)

    home_phone: str | None = Field(default=None, max_length=50)
    personal_email: str | None = Field(default=None, max_length=255)
    work_email: str | None = Field(default=None, max_length=255)

    cost_center: str | None = Field(default=None, max_length=100)
    employment_status_id: int | None = None
    employment_status_value: int | None = Field(
        default=None,
        description=(
            "IGA-friendly alternative to employment_status_id. Resolves a "
            "status by its stable numeric value (e.g. 1=Active, 0=Not Active, "
            "3=Terminated). Mutually exclusive with employment_status_id."
        ),
    )
    department_id: int | None = None
    job_title_id: int | None = None
    hire_date: date | None = None
    termination_date: date | None = None
    supervisor_id: int | None = None
    location_id: int | None = None
    # Merge semantics: keys present here are set/overwritten; keys omitted are
    # left as-is. Coerced/validated against the registry in the route.
    custom_fields: dict[str, Any] | None = None

    @field_validator("ssn")
    @classmethod
    def _validate_ssn(cls, v: str | None) -> str | None:
        return _normalize_ssn_field(v)
