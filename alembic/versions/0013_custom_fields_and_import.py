"""custom fields, import wizard tables, and nullable hire_date

Adds the machinery behind the Data Import wizard and admin-defined custom
attributes:

- employees.custom_fields: JSON bag of admin-defined attribute values, keyed by
  CustomFieldDefinition.key. Defaults to an empty object for existing rows.
- employees.hire_date: relaxed to NULLABLE. Bulk imports and some source systems
  don't carry a hire date; the app no longer forces one.
- custom_field_definitions: registry describing each custom attribute (key,
  label, data_type, order, export flag).
- import_profiles: saved, reusable column mappings per customer.
- import_batches: transient per-run wizard state (parsed rows + working mapping).

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- employees: custom_fields bag + nullable hire_date ---
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "custom_fields",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.alter_column("hire_date", existing_type=sa.Date(), nullable=True)

    # --- custom field registry ---
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "include_in_export", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        op.f("ix_custom_field_definitions_key"),
        "custom_field_definitions",
        ["key"],
        unique=True,
    )

    # --- saved reusable mappings ---
    op.create_table(
        "import_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("source_columns", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("column_map", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("value_map", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_import_profiles_name"),
    )

    # --- transient wizard runs ---
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="uploaded"),
        sa.Column("records", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_columns", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("column_map", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("value_map", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=150), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["import_profiles.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        op.f("ix_import_batches_status"), "import_batches", ["status"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_import_batches_status"), table_name="import_batches")
    op.drop_table("import_batches")
    op.drop_table("import_profiles")
    op.drop_index(
        op.f("ix_custom_field_definitions_key"),
        table_name="custom_field_definitions",
    )
    op.drop_table("custom_field_definitions")

    with op.batch_alter_table("employees", schema=None) as batch_op:
        # Restore NOT NULL on hire_date. Any NULLs must be backfilled first;
        # in practice a downgrade on a POC DB is a reset, so this is acceptable.
        batch_op.alter_column("hire_date", existing_type=sa.Date(), nullable=False)
        batch_op.drop_column("custom_fields")
