"""App user account model."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import TimestampMixin


class UserRole(StrEnum):
    """Authorization role governing what a user can do in the web UI.

    - ADMIN: full access, including settings and lookup management.
    - MANAGEMENT: full employee CRUD; can view (but not manage) lookups and
      the activity log; no settings access.
    - VIEW_ONLY: read-only access to employees and the activity log.

    Roles govern the web UI only; the REST API is authorized separately via
    API keys and OAuth clients.
    """

    ADMIN = "admin"
    MANAGEMENT = "management"
    VIEW_ONLY = "view_only"


class AppUser(Base, TimestampMixin):
    """Account that can sign in to the web UI.

    The `is_seeded` flag identifies the bootstrapped `robbytheadmin` account
    so the reset script can target it without affecting other accounts.

    `password_hash` is nullable: users provisioned via OIDC single sign-on have
    no local password and authenticate through their identity provider.

    `role` governs UI authorization — see `UserRole`.
    """

    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserRole.ADMIN.value
    )
    is_seeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- Role convenience helpers (used by route guards and templates) --------

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def is_management(self) -> bool:
        return self.role == UserRole.MANAGEMENT

    @property
    def is_view_only(self) -> bool:
        return self.role == UserRole.VIEW_ONLY

    @property
    def can_manage_employees(self) -> bool:
        """Add/edit/archive employees — admins and management."""
        return self.role in (UserRole.ADMIN, UserRole.MANAGEMENT)

    @property
    def can_view_lookups(self) -> bool:
        """See the lookup lists — admins and management."""
        return self.role in (UserRole.ADMIN, UserRole.MANAGEMENT)

    @property
    def role_label(self) -> str:
        """Human-friendly role name for display."""
        return {
            UserRole.ADMIN.value: "Admin",
            UserRole.MANAGEMENT.value: "Management",
            UserRole.VIEW_ONLY.value: "View Only",
        }.get(self.role, self.role)

    def __repr__(self) -> str:
        return f"<AppUser username={self.username!r} role={self.role!r}>"
