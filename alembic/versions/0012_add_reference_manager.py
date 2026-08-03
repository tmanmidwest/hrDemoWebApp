"""add is_reference_manager to employees and toggle to app_config

Adds support for *static reference managers* — stand-in manager records (e.g.
"margaretmanager") that can be assigned as a supervisor but are hidden from the
API/MCP employee list, CSV export, and reports so downstream systems never try
to provision or update them.

- employees.is_reference_manager: nullable-safe boolean, default False, indexed.
  All pre-existing rows become non-reference-managers, so behaviour is unchanged
  until an admin explicitly flags a row (or seeds Margaret).
- app_config.reference_managers_enabled: boolean feature toggle (default False)
  gating the UI affordances for creating/marking reference managers.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_reference_manager",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index(
            batch_op.f("ix_employees_is_reference_manager"),
            ["is_reference_manager"],
        )

    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "reference_managers_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("reference_managers_enabled")

    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_employees_is_reference_manager"))
        batch_op.drop_column("is_reference_manager")
