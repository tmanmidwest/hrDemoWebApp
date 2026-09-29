"""add is_required to custom_field_definitions

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing custom fields stay optional: everything created before this
    # migration (including fields the import wizard auto-created) predates the
    # notion of "required", and flipping them on would invalidate records that
    # are already in the database.
    op.add_column(
        "custom_field_definitions",
        sa.Column(
            "is_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("custom_field_definitions", "is_required")
