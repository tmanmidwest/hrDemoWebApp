"""Custom field definitions — the registry for admin-defined employee attributes.

The employee record carries a free-form ``custom_fields`` JSON bag (see
:class:`app.models.employee.Employee`). This table is the *schema* for that bag:
it names each attribute, gives it a human label and a data type, and controls
display order and whether it round-trips through CSV export.

Why a registry (rather than just an open JSON blob):

* The API can advertise a stable, typed shape to downstream systems (Saviynt).
* The import wizard and the employee UI can render the right controls and
  coerce/validate values per type.
* Export knows exactly which keys to emit as columns.

Keys are slugs (``worker_type``, ``cost_center_code``) — stable, machine-facing
identifiers. Labels are what a human sees.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import TimestampMixin

# Supported value types. Kept deliberately small; each maps to a coercion rule
# in app.services.custom_fields.
DATA_TYPES = ("text", "number", "boolean", "date")


class CustomFieldDefinition(Base, TimestampMixin):
    """One admin-defined custom attribute available on every employee."""

    __tablename__ = "custom_field_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Machine-facing slug, unique. This is the key used inside the employee's
    # ``custom_fields`` JSON and the header used in CSV import/export.
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Human-facing label shown in the UI.
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    # One of DATA_TYPES.
    data_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text"
    )

    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Sort order for display; lower first.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Whether this field is emitted as a column on CSV export.
    include_in_export: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    def __repr__(self) -> str:
        return f"<CustomFieldDefinition key={self.key!r} type={self.data_type!r}>"
