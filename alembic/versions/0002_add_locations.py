"""add locations lookup and employees.location_id

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-05

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_locations_name"),
    )

    # SQLite cannot ALTER a table to add a column with an FK in place; batch
    # mode rebuilds the table so the named FK constraint is created cleanly.
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.add_column(sa.Column("location_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_employees_location_id"), ["location_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_employees_location_id_locations",
            "locations",
            ["location_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_employees_location_id_locations", type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_employees_location_id"))
        batch_op.drop_column("location_id")

    op.drop_table("locations")
