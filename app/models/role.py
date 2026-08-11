"""Business-role catalog and per-console-user role assignments.

These model the *access roles* an IGA platform (e.g. Saviynt) governs: a
catalog of named roles, plus the grants that assign those roles to console
accounts (`AppUser`). This is distinct from `AppUser.role` / `UserRole`, which
is the coarse web-UI authorization level (admin / management / view_only).

The IGA connector reads the catalog and the assignments to reconcile state,
and provisions / deprovisions by granting / revoking `RoleAssignment` rows.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.app_user import AppUser


class AccessLevel(StrEnum):
    """Descriptive access a role is intended to convey, for the catalog UI.

    This is *documentation of intent* only — a human-readable annotation shown
    in the role editor and catalog. It is deliberately NOT exposed through the
    REST API and does NOT enforce anything in this app: the IGA reads the role
    as an entitlement and enforces the corresponding access in downstream target
    systems. Left unset (NULL) when an admin hasn't classified the role yet.
    """

    VIEW_ONLY = "view_only"
    MANAGE_EMPLOYEES = "manage_employees"
    FULL_SYSTEM = "full_system"

    @property
    def label(self) -> str:
        return {
            "view_only": "View only",
            "manage_employees": "Manage employees",
            "full_system": "Full system",
        }[self.value]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """(value, label) pairs for rendering a <select>."""
        return [(level.value, level.label) for level in cls]


class Role(Base, TimestampMixin):
    """A named access role that can be granted to console accounts.

    `name` is the natural key IGA connectors correlate on, so it is unique.
    Deactivating a role (`is_active = False`) hides it from provisioning
    without deleting the history of who held it.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # UI-only classification of the access this role conveys. NOT exposed via
    # the REST API (see AccessLevel); purely descriptive for the catalog.
    access_level: Mapped[str | None] = mapped_column(String(30), nullable=True)

    assignments: Mapped[list[RoleAssignment]] = relationship(
        "RoleAssignment",
        back_populates="role",
        cascade="all, delete-orphan",
    )

    @property
    def access_level_label(self) -> str:
        """Human-friendly label for the access level, or an em dash if unset."""
        try:
            return AccessLevel(self.access_level).label
        except ValueError:
            return "—"

    def __repr__(self) -> str:
        return f"<Role name={self.name!r} is_active={self.is_active}>"


class RoleAssignment(Base, TimestampMixin):
    """A grant of one `Role` to one console account (`AppUser`).

    The `(user_id, role_id)` pair is unique — a user holds a given role at most
    once. `created_at` doubles as the "granted at" timestamp; `updated_since`
    reconciliation feeds key off `updated_at`.
    """

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_role_assignment_user_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    user: Mapped[AppUser] = relationship("AppUser")
    role: Mapped[Role] = relationship("Role", back_populates="assignments")

    def __repr__(self) -> str:
        return f"<RoleAssignment user_id={self.user_id} role_id={self.role_id}>"
