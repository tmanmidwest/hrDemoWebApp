"""drop roles and role_assignments tables

Roles are now the fixed, enum-backed console-account authorization levels
(view_only / management / admin) carried on ``app_users.role``. The separate
access-role catalog (`roles`) and its per-user grants (`role_assignments`) are
removed: a user holds exactly one role, the catalog is published read-only from
the enum at ``GET /api/v1/roles``, and assignment is the ``role`` attribute set
through the users API.

The downgrade recreates both tables and re-seeds the former default catalog
(with access levels) so the schema can be fully restored.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (name, description, access_level) — the former seeded catalog, restored on
# downgrade so the reversed schema matches the 0016 + 0017 state.
DEFAULT_ROLES: list[tuple[str, str, str]] = [
    ("HR Administrator", "Full administrative access to HR records and settings.", "full_system"),
    ("HR Analyst", "Read and reporting access to HR data.", "view_only"),
    ("Payroll Processor", "Access to payroll processing functions.", "manage_employees"),
    ("Recruiter", "Access to recruiting and candidate records.", "manage_employees"),
    ("Manager Self-Service", "Manager access to direct-report information.", "view_only"),
    ("Employee Self-Service", "Basic self-service access to one's own records.", "view_only"),
    ("IT Auditor", "Read-only access for audit and compliance review.", "view_only"),
]


def upgrade() -> None:
    with op.batch_alter_table("role_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_assignments_role_id"))
        batch_op.drop_index(batch_op.f("ix_role_assignments_user_id"))
    op.drop_table("role_assignments")
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_roles_name"))
    op.drop_table("roles")


def downgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("access_level", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_roles_name"), ["name"], unique=True)

    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "role_id", name="uq_role_assignment_user_role"
        ),
    )
    with op.batch_alter_table("role_assignments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_role_assignments_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_role_assignments_role_id"), ["role_id"], unique=False
        )

    now = datetime.now(UTC)
    roles_table = sa.table(
        "roles",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("access_level", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        roles_table,
        [
            {
                "name": name,
                "description": description,
                "is_active": True,
                "access_level": access_level,
                "created_at": now,
                "updated_at": now,
            }
            for name, description, access_level in DEFAULT_ROLES
        ],
    )
