"""add allowed_domains to auth_providers (SSO email-domain allowlist)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-02

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default="" backfills existing rows (no restriction); the ORM model
    # keeps its own default so new rows don't depend on the server default.
    with op.batch_alter_table("auth_providers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allowed_domains",
                sa.String(length=500),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("auth_providers", schema=None) as batch_op:
        batch_op.drop_column("allowed_domains")
