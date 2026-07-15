"""Aggregate reporting endpoints.

Read-only, `GROUP BY`-style summaries over the employee and audit data. These
are what the MCP server's report tools call, but they're plain REST and usable
by any bearer-authenticated caller holding the ``reports:read`` scope.

Every call records an audit event so report access shows up in the Activity log
alongside everything else.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AuditEvent,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    Location,
)
from app.schemas.reports import (
    ActivityBucket,
    ActivityReport,
    HeadcountBucket,
    HeadcountReport,
    OrgManager,
    OrgReport,
)
from app.services.audit import principal_actor, record_event
from app.services.auth import Principal, require_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# Headcount
# ---------------------------------------------------------------------------

# Each grouping maps to the model we group on: the FK column on Employee, the
# related model's id/label columns, and whether the FK is nullable (so we know
# to LEFT JOIN and surface an "Unassigned" bucket).
HeadcountGroupBy = Literal["department", "location", "status", "job_title", "country"]

_HEADCOUNT_GROUPS: dict[str, dict[str, Any]] = {
    "department": {
        "model": Department,
        "fk": Employee.department_id,
        "label": Department.name,
        "nullable": False,
    },
    "location": {
        "model": Location,
        "fk": Employee.location_id,
        "label": Location.name,
        "nullable": True,
    },
    "status": {
        "model": EmploymentStatus,
        "fk": Employee.employment_status_id,
        "label": EmploymentStatus.label,
        "nullable": False,
    },
    "job_title": {
        "model": JobTitle,
        "fk": Employee.job_title_id,
        "label": JobTitle.name,
        "nullable": False,
    },
    "country": {
        "model": Country,
        "fk": Employee.country_id,
        "label": Country.name,
        "nullable": False,
    },
}


@router.get("/headcount", response_model=HeadcountReport)
def headcount_report(
    request: Request,
    group_by: HeadcountGroupBy = Query(
        default="department",
        description="Dimension to group the headcount by.",
    ),
    include_archived: bool = Query(
        default=False,
        description="Include archived (soft-deleted) employees in the counts.",
    ),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("reports:read")),
) -> HeadcountReport:
    """Count employees grouped by department, location, status, title, or country."""
    cfg = _HEADCOUNT_GROUPS[group_by]
    model = cfg["model"]
    fk = cfg["fk"]
    label_col = cfg["label"]
    nullable = bool(cfg["nullable"])

    # Inner-join from the lookup and count matching employees per group. Groups
    # with no (matching) employees simply don't appear — that's the intended
    # headcount semantics. Employees with a NULL nullable FK are counted below
    # as a separate "Unassigned" bucket.
    query = db.query(model.id, label_col, func.count(Employee.id)).join(
        Employee, fk == model.id
    )
    if not include_archived:
        query = query.filter(Employee.is_archived.is_(False))
    grouped = query.group_by(model.id, label_col).all()

    buckets = [
        HeadcountBucket(key=row[0], label=row[1], count=row[2]) for row in grouped
    ]

    # For a nullable FK, employees with no value don't appear via the join;
    # count them separately as an "Unassigned" bucket.
    if nullable:
        unassigned_q = db.query(func.count(Employee.id)).filter(fk.is_(None))
        if not include_archived:
            unassigned_q = unassigned_q.filter(Employee.is_archived.is_(False))
        unassigned = unassigned_q.scalar() or 0
        if unassigned:
            buckets.append(
                HeadcountBucket(key=None, label="Unassigned", count=unassigned)
            )

    buckets.sort(key=lambda b: b.count, reverse=True)
    total = sum(b.count for b in buckets)

    record_event(
        category="report",
        event_type="report.headcount",
        **principal_actor(principal),
        target_type="report",
        target_label=f"headcount by {group_by}",
        message=f"Ran headcount report grouped by {group_by}",
        detail={"group_by": group_by, "include_archived": include_archived, "total": total},
        request=request,
    )
    return HeadcountReport(
        group_by=group_by,
        include_archived=include_archived,
        total=total,
        buckets=buckets,
        generated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Org structure
# ---------------------------------------------------------------------------


@router.get("/org", response_model=OrgReport)
def org_report(
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Max number of managers to return (largest spans first).",
    ),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("reports:read")),
) -> OrgReport:
    """Org-structure summary: managers, spans of control, and rollup totals.

    Only non-archived employees are counted, on both the manager and report side.
    """
    total_employees = (
        db.query(func.count(Employee.id))
        .filter(Employee.is_archived.is_(False))
        .scalar()
        or 0
    )
    without_supervisor = (
        db.query(func.count(Employee.id))
        .filter(Employee.is_archived.is_(False), Employee.supervisor_id.is_(None))
        .scalar()
        or 0
    )

    # Direct-report counts per supervisor (reports must be non-archived).
    report_counts = (
        db.query(Employee.supervisor_id, func.count(Employee.id))
        .filter(
            Employee.is_archived.is_(False),
            Employee.supervisor_id.is_not(None),
        )
        .group_by(Employee.supervisor_id)
        .all()
    )
    # supervisor_id is filtered non-null above, so every key is a real int.
    counts_by_mgr: dict[int, int] = {
        sup_id: n for sup_id, n in report_counts if sup_id is not None
    }

    total_managers = len(counts_by_mgr)
    individual_contributors = total_employees - total_managers
    spans = list(counts_by_mgr.values())
    max_span = max(spans) if spans else 0
    avg_span = round(sum(spans) / total_managers, 2) if total_managers else 0.0

    # Resolve the top managers to names. A manager could itself be archived (an
    # archived boss with active reports); include them so the reports aren't
    # orphaned, but they simply won't be counted in total_employees.
    managers: list[OrgManager] = []
    if counts_by_mgr:
        top_ids = sorted(counts_by_mgr, key=lambda i: counts_by_mgr[i], reverse=True)[
            :limit
        ]
        rows = db.query(Employee).filter(Employee.id.in_(top_ids)).all()
        by_id = {e.id: e for e in rows}
        for mgr_id in top_ids:
            emp = by_id.get(mgr_id)
            if emp is None:
                continue
            managers.append(
                OrgManager(
                    employee_id=emp.id,
                    employee_number=emp.employee_number,
                    name=emp.full_name,
                    direct_reports=counts_by_mgr[mgr_id],
                )
            )

    record_event(
        category="report",
        event_type="report.org",
        **principal_actor(principal),
        target_type="report",
        target_label="org structure",
        message="Ran org-structure report",
        detail={"total_employees": total_employees, "total_managers": total_managers},
        request=request,
    )
    return OrgReport(
        total_employees=total_employees,
        total_managers=total_managers,
        individual_contributors=individual_contributors,
        without_supervisor=without_supervisor,
        max_span=max_span,
        avg_span=avg_span,
        managers=managers,
        generated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


def _activity_buckets(
    db: Session, since: datetime, column: Any
) -> list[ActivityBucket]:
    """Count audit events since `since`, grouped by the given column."""
    rows = (
        db.query(column, func.count(AuditEvent.id))
        .filter(AuditEvent.occurred_at >= since)
        .group_by(column)
        .order_by(func.count(AuditEvent.id).desc())
        .all()
    )
    return [ActivityBucket(key=str(key), count=count) for key, count in rows]


@router.get("/activity", response_model=ActivityReport)
def activity_report(
    request: Request,
    days: int = Query(
        default=7,
        ge=1,
        le=365,
        description="Trailing window size in days.",
    ),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("reports:read")),
) -> ActivityReport:
    """Summarize audit/activity events over a trailing window.

    Note: the underlying events are pruned on the app's audit-retention schedule,
    so a window longer than the retention period returns only what's still kept.
    """
    since = datetime.now(UTC) - timedelta(days=days)

    total_events = (
        db.query(func.count(AuditEvent.id))
        .filter(AuditEvent.occurred_at >= since)
        .scalar()
        or 0
    )
    by_category = _activity_buckets(db, since, AuditEvent.category)
    by_event_type = _activity_buckets(db, since, AuditEvent.event_type)
    by_outcome = _activity_buckets(db, since, AuditEvent.outcome)

    record_event(
        category="report",
        event_type="report.activity",
        **principal_actor(principal),
        target_type="report",
        target_label=f"activity ({days}d)",
        message=f"Ran activity report over {days} day window",
        detail={"days": days, "total_events": total_events},
        request=request,
    )
    return ActivityReport(
        window_days=days,
        since=since,
        total_events=total_events,
        by_category=by_category,
        by_event_type=by_event_type,
        by_outcome=by_outcome,
        generated_at=datetime.now(UTC),
    )
