"""make department, job title, and employment status optional on employees

Relaxes ``employees.department_id``, ``job_title_id``, and
``employment_status_id`` to NULLABLE so an employee can be added with only the
essentials (number, name, country). ``hire_date`` and ``supervisor_id`` were
already nullable. ``country_id`` remains required.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.alter_column(
            "employment_status_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.alter_column(
            "department_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.alter_column(
            "job_title_id", existing_type=sa.Integer(), nullable=True
        )


def downgrade() -> None:
    # Restoring NOT NULL requires every row to have a value; on a POC DB a
    # downgrade is effectively a reset, so this is acceptable.
    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.alter_column(
            "job_title_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column(
            "department_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column(
            "employment_status_id", existing_type=sa.Integer(), nullable=False
        )
