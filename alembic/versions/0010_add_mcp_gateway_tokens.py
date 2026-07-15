"""add mcp_gateway_tokens table

Multiple named, individually revocable inbound MCP gateway tokens. The app syncs
active token hashes to the data volume for the DB-less MCP server to verify
against.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_gateway_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_prefix", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("mcp_gateway_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mcp_gateway_tokens_token_hash"),
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("mcp_gateway_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_mcp_gateway_tokens_token_hash"))
    op.drop_table("mcp_gateway_tokens")
