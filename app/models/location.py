"""Location lookup table model."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import TimestampMixin


class Location(Base, TimestampMixin):
    """A physical or organizational location an employee can be assigned to.

    Standalone lookup (like Department, but with no child rows). Employees
    reference it via an OPTIONAL foreign key — location is not required.
    """

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Location name={self.name!r}>"
