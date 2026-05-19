"""Employment status lookup table model."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import TimestampMixin


class EmploymentStatus(Base, TimestampMixin):
    """Employment status. Holds both a numeric value (sent to integrating systems)
    and an is_active_status boolean (drives lifecycle decisions in IGA tools).
    """

    __tablename__ = "employment_statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<EmploymentStatus label={self.label!r} value={self.value}>"
