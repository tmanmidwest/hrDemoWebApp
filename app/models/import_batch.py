"""Import wizard state — reusable profiles and per-run batches.

The Data Import wizard walks an admin through Upload → Map → Resolve → Preview →
Commit. Two tables back it:

* :class:`ImportProfile` — a *saved, reusable* mapping for a given customer's
  file shape. Map once ("Progress Rail POC"), re-run every refresh in one click.
* :class:`ImportBatch` — the *transient state* of one run in progress: the parsed
  rows plus the working mapping/options, carried across wizard steps by id and
  finalized (or abandoned) at the end.

Both store their flexible parts as JSON so the wizard can evolve the mapping
vocabulary without a migration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import TimestampMixin


class ImportProfile(Base, TimestampMixin):
    """A saved column mapping for a recurring customer import."""

    __tablename__ = "import_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The source headers this profile was built for (used to suggest a match on
    # a fresh upload).
    source_columns: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    # source header -> target spec, e.g. {"Dept Descr": {"target": "department"}}.
    column_map: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    # per-column value translations, e.g. {"Loc Country": {"USA": "United States"}}.
    value_map: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    # wizard options, e.g. {"auto_create_lookups": true}.
    options: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_by: Mapped[str | None] = mapped_column(String(150), nullable=True)

    def __repr__(self) -> str:
        return f"<ImportProfile name={self.name!r}>"


# Lifecycle of a single wizard run.
BATCH_STATUSES = ("uploaded", "mapped", "resolved", "committed", "cancelled")


class ImportBatch(Base, TimestampMixin):
    """Transient per-run state for one pass through the import wizard."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded", index=True
    )

    # Parsed rows exactly as read from the file: a list of {header: value} dicts.
    records: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_columns: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )

    column_map: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    value_map: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    options: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_profiles.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # Summary counts once committed (created/updated/skipped), for the done page.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<ImportBatch id={self.id} status={self.status!r}>"
