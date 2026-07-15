"""Pydantic schemas for the reporting API.

These are read-only aggregate views over the employee and audit data. They back
the ``/api/v1/reports/*`` endpoints and, in turn, the MCP server's report tools.
Nothing here writes — every response is a computed summary.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Headcount
# ---------------------------------------------------------------------------


class HeadcountBucket(BaseModel):
    """One row of a grouped headcount: a category and how many employees fall in it."""

    key: str | int | None = Field(
        description="The group's identifier (e.g. department id). Null for the "
        "'unassigned' bucket when the grouping column is nullable."
    )
    label: str = Field(description="Human-readable group name, e.g. 'Engineering'.")
    count: int = Field(description="Number of employees in this group.")


class HeadcountReport(BaseModel):
    """Employee counts grouped by a single dimension."""

    group_by: str = Field(description="The dimension the counts are grouped by.")
    include_archived: bool = Field(
        description="Whether archived (soft-deleted) employees were counted."
    )
    total: int = Field(description="Total employees across all groups.")
    buckets: list[HeadcountBucket]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Org structure
# ---------------------------------------------------------------------------


class OrgManager(BaseModel):
    """A supervisor and their span of control (direct report count)."""

    employee_id: int
    employee_number: str
    name: str
    direct_reports: int


class OrgReport(BaseModel):
    """Organization-structure summary: managers, spans of control, and totals."""

    total_employees: int = Field(description="Non-archived employees counted.")
    total_managers: int = Field(
        description="Employees who have at least one direct report."
    )
    individual_contributors: int = Field(
        description="Non-archived employees with no direct reports."
    )
    without_supervisor: int = Field(
        description="Non-archived employees whose supervisor_id is null (e.g. the "
        "top of the org, or unassigned)."
    )
    max_span: int = Field(description="Largest number of direct reports any manager has.")
    avg_span: float = Field(
        description="Average direct reports per manager (0 when there are no managers)."
    )
    managers: list[OrgManager] = Field(
        description="Managers ordered by span of control, largest first."
    )
    generated_at: datetime


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


class ActivityBucket(BaseModel):
    """A count of audit events sharing one key (category, event_type, or outcome)."""

    key: str
    count: int


class ActivityReport(BaseModel):
    """Summary of audit/activity events over a trailing time window."""

    window_days: int = Field(description="Size of the trailing window, in days.")
    since: datetime = Field(description="Start of the window (inclusive).")
    total_events: int
    by_category: list[ActivityBucket]
    by_event_type: list[ActivityBucket]
    by_outcome: list[ActivityBucket]
    generated_at: datetime
