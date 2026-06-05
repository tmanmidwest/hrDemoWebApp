"""Pydantic schemas for the Employee API.

Responses include nested lookup objects (country, department, etc.) so IGA
systems get all the data they need in one call. Writes accept only FK IDs.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    hire_date: date
    termination_date: date | None
    supervisor: EmployeeSupervisorRef | None

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
    hire_date: date

    # Optional
    middle_name: str | None = Field(default=None, max_length=100)
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

    @model_validator(mode="after")
    def _validate_dates(self) -> EmployeeCreate:
        if self.termination_date is not None and self.termination_date < self.hire_date:
            raise ValueError("termination_date must be on or after hire_date")
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
