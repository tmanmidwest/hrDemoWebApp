"""Pydantic schemas for lookup-table CRUD endpoints.

Naming convention:
- `*Out`     — what the API returns (full record, including FKs as nested objects)
- `*Create`  — what the client sends to POST
- `*Update`  — what the client sends to PATCH (all fields optional)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Country
# ---------------------------------------------------------------------------


class CountryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CountryCreate(BaseModel):
    code: str = Field(min_length=2, max_length=2, description="ISO-3166-1 alpha-2")
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class CountryUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=2)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# State / Province
# ---------------------------------------------------------------------------


class StateProvinceCountryOut(BaseModel):
    """Lightweight country reference embedded in state/province responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class StateProvinceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    country_id: int
    code: str | None
    name: str
    is_active: bool
    country: StateProvinceCountryOut
    created_at: datetime
    updated_at: datetime


class StateProvinceCreate(BaseModel):
    country_id: int
    code: str | None = Field(default=None, max_length=10)
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class StateProvinceUpdate(BaseModel):
    country_id: int | None = None
    code: str | None = Field(default=None, max_length=10)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Employment Status
# ---------------------------------------------------------------------------


class EmploymentStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    value: int
    is_active_status: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class EmploymentStatusCreate(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    value: int
    is_active_status: bool = False
    # is_system is intentionally NOT exposed on create — only seed data can set it


class EmploymentStatusUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=50)
    value: int | None = None
    is_active_status: bool | None = None


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Job Title
# ---------------------------------------------------------------------------


class JobTitleDepartmentOut(BaseModel):
    """Lightweight department reference embedded in job title responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class JobTitleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    department_id: int
    name: str
    is_active: bool
    department: JobTitleDepartmentOut
    created_at: datetime
    updated_at: datetime


class JobTitleCreate(BaseModel):
    department_id: int
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class JobTitleUpdate(BaseModel):
    department_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
