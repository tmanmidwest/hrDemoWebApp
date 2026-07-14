"""add role column to app_users

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14

Existing accounts predate the role concept and had full admin power, so they
default to 'admin' to preserve their access. New SSO-provisioned users are
created as 'view_only' in application code.

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_users",
        sa.Column("role", sa.String(20), nullable=False, server_default="admin"),
    )


def downgrade() -> None:
    op.drop_column("app_users", "role")
