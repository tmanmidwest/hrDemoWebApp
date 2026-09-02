"""add tagline + banner text fields to app_branding

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-02

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default preserves the previously hardcoded sidebar/login sub-text
    # for existing installs; new/blank values hide the line.
    op.add_column(
        "app_branding",
        sa.Column(
            "brand_tagline",
            sa.String(length=120),
            nullable=False,
            server_default="POC · non-production",
        ),
    )
    op.add_column(
        "app_branding",
        sa.Column(
            "brand_banner",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_branding", "brand_banner")
    op.drop_column("app_branding", "brand_tagline")
