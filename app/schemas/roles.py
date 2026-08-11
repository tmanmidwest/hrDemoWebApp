"""Pydantic schemas for the access-role catalog and role-assignment API.

Naming convention (matches app/schemas/lookups.py):
- `*Out`     — what the API returns
- `*Create`  — what the client sends to POST
- `*Update`  — what the client sends to PATCH (all fields optional)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Role catalog
# ---------------------------------------------------------------------------


class RoleOut(BaseModel):
    """An access role as returned by the roles API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RoleCreate(BaseModel):
    """Request body for creating an access role."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class RoleUpdate(BaseModel):
    """Request body for updating an access role. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Role assignments (grants of a role to a console account)
# ---------------------------------------------------------------------------


class RoleAssignmentRoleRef(BaseModel):
    """The role side of an assignment, embedded in assignment responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool


class RoleAssignmentUserRef(BaseModel):
    """The console-account side of an assignment, embedded in responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class RoleAssignmentOut(BaseModel):
    """A grant of a role to a console account.

    `granted_at` is the assignment's creation time; `updated_at` backs the
    `updated_since` reconciliation feed. Both the user and role are embedded so
    an IGA connector can reconcile without extra lookups.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user: RoleAssignmentUserRef
    role: RoleAssignmentRoleRef
    granted_at: datetime = Field(
        validation_alias="created_at",
        description="When the role was granted (provisioned).",
    )
    updated_at: datetime


class RoleGrant(BaseModel):
    """Request body to grant (provision) a role to a console account."""

    role_id: int = Field(description="ID of the role to grant.")
