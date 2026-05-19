"""Employee model — the central table."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin
from app.models.country import Country
from app.models.department import Department
from app.models.employment_status import EmploymentStatus
from app.models.job_title import JobTitle
from app.models.state_province import StateProvince


class Employee(Base, TimestampMixin):
    """Employee record. The supervisor relationship is self-referential."""

    __tablename__ = "employees"
    __table_args__ = (
        # Prevent self-supervision at the DB level. NULL is allowed (first employee).
        CheckConstraint(
            "supervisor_id IS NULL OR supervisor_id != id",
            name="ck_employee_no_self_supervision",
        ),
        Index("ix_employees_lastname_firstname", "last_name", "first_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity
    employee_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Address
    address_line_1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country_id: Mapped[int] = mapped_column(
        ForeignKey("countries.id"), nullable=False, index=True
    )
    state_province_id: Mapped[int | None] = mapped_column(
        ForeignKey("states_provinces.id"), nullable=True
    )
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Contact
    home_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    personal_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Employment
    cost_center: Mapped[str | None] = mapped_column(String(100), nullable=True)
    employment_status_id: Mapped[int] = mapped_column(
        ForeignKey("employment_statuses.id"), nullable=False, index=True
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    job_title_id: Mapped[int] = mapped_column(
        ForeignKey("job_titles.id"), nullable=False
    )
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Supervisor — self-referential FK
    supervisor_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )

    # Soft-delete
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    country: Mapped[Country] = relationship("Country", lazy="joined")
    state_province: Mapped[StateProvince | None] = relationship(
        "StateProvince", lazy="joined"
    )
    employment_status: Mapped[EmploymentStatus] = relationship(
        "EmploymentStatus", lazy="joined"
    )
    department: Mapped[Department] = relationship("Department", lazy="joined")
    job_title: Mapped[JobTitle] = relationship("JobTitle", lazy="joined")

    # Self-referential — the supervisor is another Employee.
    # remote_side tells SQLAlchemy which side is the "one" in the many-to-one.
    supervisor: Mapped[Employee | None] = relationship(
        "Employee",
        remote_side="Employee.id",
        foreign_keys="Employee.supervisor_id",
        backref="direct_reports",
        lazy="joined",
        join_depth=1,  # Avoid loading the supervisor's supervisor's supervisor...
    )

    @property
    def full_name(self) -> str:
        """First + last name for display."""
        return f"{self.first_name} {self.last_name}"

    def __repr__(self) -> str:
        return (
            f"<Employee id={self.id} number={self.employee_number!r} "
            f"name={self.full_name!r}>"
        )

    # Convenience for SQL expressions
    @classmethod
    def employee_number_normalized(cls) -> object:
        """SQL expression for case-insensitive employee_number comparison."""
        return func.lower(cls.employee_number)
