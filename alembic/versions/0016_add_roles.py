"""add roles and role_assignments tables

Introduces an access-role catalog (`roles`) and grants of those roles to
console accounts (`role_assignments`), so an IGA platform can pull role info
per user and provision / deprovision by granting / revoking assignments.

A small default catalog is seeded here (rather than in the runtime seeder) so
that both new databases and existing demo instances gain a usable set of roles
on upgrade. Deletions of these rows persist — the seed runs once, with the
migration.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (name, description) — a demo catalog spanning common HR/IT access roles.
DEFAULT_ROLES: list[tuple[str, str]] = [
    ("HR Administrator", "Full administrative access to HR records and settings."),
    ("HR Analyst", "Read and reporting access to HR data."),
    ("Payroll Processor", "Access to payroll processing functions."),
    ("Recruiter", "Access to recruiting and candidate records."),
    ("Manager Self-Service", "Manager access to direct-report information."),
    ("Employee Self-Service", "Basic self-service access to one's own records."),
    ("IT Auditor", "Read-only access for audit and compliance review."),
]


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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

    # Seed the default role catalog.
    now = datetime.now(UTC)
    roles_table = sa.table(
        "roles",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_active", sa.Boolean),
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
                "created_at": now,
                "updated_at": now,
            }
            for name, description in DEFAULT_ROLES
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table("role_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_assignments_role_id"))
        batch_op.drop_index(batch_op.f("ix_role_assignments_user_id"))
    op.drop_table("role_assignments")
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_roles_name"))
    op.drop_table("roles")
