"""State/Province lookup table model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.country import Country


class StateProvince(Base, TimestampMixin):
    """State, province, or other subdivision belonging to a country."""

    __tablename__ = "states_provinces"
    __table_args__ = (
        UniqueConstraint("country_id", "name", name="uq_states_country_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(
        ForeignKey("countries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    country: Mapped[Country] = relationship("Country", back_populates="states_provinces")

    def __repr__(self) -> str:
        return f"<StateProvince name={self.name!r} country_id={self.country_id}>"
