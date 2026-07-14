"""add scopes column to api_keys

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14

Existing keys predate scoped permissions and had full access, so they default
to 'admin' (the wildcard scope) to preserve their behavior. New keys are created
with an explicit scope set.

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("scopes", sa.String(500), nullable=False, server_default="admin"),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "scopes")
