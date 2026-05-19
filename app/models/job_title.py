"""Job title lookup table model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.department import Department


class JobTitle(Base, TimestampMixin):
    """Job title belonging to a department."""

    __tablename__ = "job_titles"
    __table_args__ = (
        UniqueConstraint("department_id", "name", name="uq_jobtitle_dept_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    department: Mapped[Department] = relationship("Department", back_populates="job_titles")

    def __repr__(self) -> str:
        return f"<JobTitle name={self.name!r} department_id={self.department_id}>"
