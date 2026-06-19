"""add OIDC auth providers, user identities, and nullable password_hash

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-18

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("issuer_url", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_encrypted", sa.String(length=1000), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["app_users.id"],
            name="fk_auth_providers_created_by_user_id_app_users",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_auth_providers_slug"),
    )
    op.create_index(
        op.f("ix_auth_providers_slug"), "auth_providers", ["slug"], unique=True
    )

    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_users.id"],
            name="fk_user_identities_user_id_app_users", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["auth_providers.id"],
            name="fk_user_identities_provider_id_auth_providers", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id", "subject", name="uq_user_identities_provider_subject"
        ),
    )
    op.create_index(
        op.f("ix_user_identities_user_id"), "user_identities", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_identities_provider_id"), "user_identities", ["provider_id"], unique=False
    )

    # OIDC-provisioned users have no local password. SQLite can't ALTER a column
    # in place, so batch mode rebuilds the table with password_hash nullable.
    with op.batch_alter_table("app_users", schema=None) as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(length=255),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("app_users", schema=None) as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(length=255),
            nullable=False,
        )

    op.drop_index(op.f("ix_user_identities_provider_id"), table_name="user_identities")
    op.drop_index(op.f("ix_user_identities_user_id"), table_name="user_identities")
    op.drop_table("user_identities")

    op.drop_index(op.f("ix_auth_providers_slug"), table_name="auth_providers")
    op.drop_table("auth_providers")
