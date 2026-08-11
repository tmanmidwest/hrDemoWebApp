"""add descriptive access_level to roles

A UI-only classification of the access a role conveys (view_only /
manage_employees / full_system). It is intentionally NOT exposed through the
REST API — the IGA consumes roles as entitlements and enforces access
downstream; this column just documents intent for the catalog UI.

The seeded demo roles are backfilled with a sensible level so the classification
is visible out of the box. Nullable, so custom roles start unclassified.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# role name -> default access level, applied to the seeded catalog only.
SEED_ACCESS_LEVELS: dict[str, str] = {
    "HR Administrator": "full_system",
    "HR Analyst": "view_only",
    "Payroll Processor": "manage_employees",
    "Recruiter": "manage_employees",
    "Manager Self-Service": "view_only",
    "Employee Self-Service": "view_only",
    "IT Auditor": "view_only",
}


def upgrade() -> None:
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("access_level", sa.String(length=30), nullable=True))

    # Backfill the seeded roles by name (no-op for any the admin already removed).
    roles = sa.table(
        "roles", sa.column("name", sa.String), sa.column("access_level", sa.String)
    )
    for name, level in SEED_ACCESS_LEVELS.items():
        op.execute(
            roles.update().where(roles.c.name == op.inline_literal(name)).values(
                access_level=op.inline_literal(level)
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_column("access_level")
