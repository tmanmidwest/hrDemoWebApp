"""add theme preference column to app_users

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-14

Nullable UI theme preference: NULL follows the OS setting, otherwise
"light" / "dark". Existing accounts default to NULL (follow OS).

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("theme", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("app_users", "theme")
