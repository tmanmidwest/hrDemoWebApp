"""add date_of_birth and ssn to employees

Adds two personal fields to the employees table:
- date_of_birth (nullable Date)
- ssn (nullable, 9 digits, no separators)

The ssn column gets a UNIQUE index so the same social security number can't be
recorded for two employees — the DB-level backstop for the duplicate-employee
check. The index is nullable, so employees with no SSN on file (including all
pre-existing rows) coexist fine.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.add_column(sa.Column("date_of_birth", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("ssn", sa.String(length=9), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_employees_ssn"), ["ssn"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_employees_ssn"))
        batch_op.drop_column("ssn")
        batch_op.drop_column("date_of_birth")
