"""track whether default org data has been seeded

Adds ``app_config.org_defaults_seeded``. Startup seeding sets it True after
seeding the default departments, job titles, and locations, and skips that step
when it's already True — so an admin's deletions of seeded org data survive a
restart/rebuild instead of reappearing.

Existing installs are marked as already-seeded (the org defaults were seeded on
some earlier startup), so deploying this fix does not re-create anything the
admin has since removed.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "org_defaults_seeded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    # An existing config row means the app has run before, so the default org
    # data was already seeded — mark it so we don't re-add deleted rows.
    op.execute("UPDATE app_config SET org_defaults_seeded = 1")


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("org_defaults_seeded")
