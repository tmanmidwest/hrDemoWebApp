"""Country lookup table model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.state_province import StateProvince


class Country(Base, TimestampMixin):
    """ISO country lookup. Seeded with ~250 ISO-3166-1 entries on first deploy."""

    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(2), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    states_provinces: Mapped[list[StateProvince]] = relationship(
        "StateProvince",
        back_populates="country",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Country code={self.code!r} name={self.name!r}>"
