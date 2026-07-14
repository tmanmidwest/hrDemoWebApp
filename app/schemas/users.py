"""Pydantic schemas for the console-user (AppUser) management API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.app_user import UserRole


class UserOut(BaseModel):
    """A console account as returned by the users API (never the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool
    is_seeded: bool
    created_at: datetime
    last_login_at: datetime | None

    # Present on the ORM object as `password_hash`; exposed only as a coarse type.
    password_hash: str | None = Field(default=None, exclude=True, repr=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auth_type(self) -> str:
        """'local' (password login) or 'sso' (provisioned via an identity provider)."""
        return "local" if self.password_hash else "sso"


class UserCreate(BaseModel):
    """Request body for creating a local console account."""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=255)
    role: UserRole = UserRole.VIEW_ONLY


class UserUpdate(BaseModel):
    """Request body for updating a console account. All fields optional."""

    username: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8, max_length=255)
    role: UserRole | None = None
