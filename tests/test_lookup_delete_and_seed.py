"""Tests for sticky org-data seeding and the department-delete confirmation flow."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Sticky seeding: deleted default org data does not reappear on re-seed
# ---------------------------------------------------------------------------


def test_org_defaults_seeded_only_once(_isolated_data_dir):
    from app.config import get_settings
    from app.db import get_session_factory
    from app.models import Department, Employee, JobTitle
    from app.models.app_config import APP_CONFIG_ID, AppConfig
    from app.services.migrations import run_migrations
    from app.services.seed_data import seed_database
    from app.services.system_config import invalidate

    run_migrations()
    db = get_session_factory()()
    settings = get_settings()

    seed_database(db, settings)
    assert db.query(Department).count() > 0
    assert db.get(AppConfig, APP_CONFIG_ID).org_defaults_seeded is True

    # Admin removes all org data.
    db.query(Employee).delete()
    db.query(JobTitle).delete()
    db.query(Department).delete()
    db.commit()

    # A restart/rebuild re-runs seeding — deleted defaults must NOT return.
    invalidate()
    seed_database(db, settings)
    assert db.query(Department).count() == 0
    assert db.query(JobTitle).count() == 0


# ---------------------------------------------------------------------------
# Department delete flow
# ---------------------------------------------------------------------------


def _seed_ref(db):
    """Return (country_id, status_id) from seeded reference data."""
    from app.models import Country, EmploymentStatus

    country = db.query(Country).first()
    status = db.query(EmploymentStatus).first()
    return country.id, status.id


def _make_dept(db, name, *, titles=(), employees=0):
    from app.models import Department, Employee, JobTitle

    dept = Department(name=name, is_active=True)
    db.add(dept)
    db.flush()
    title_rows = []
    for t in titles:
        jt = JobTitle(department_id=dept.id, name=t, is_active=True)
        db.add(jt)
        title_rows.append(jt)
    db.flush()
    country_id, status_id = _seed_ref(db)
    for i in range(employees):
        db.add(
            Employee(
                employee_number=f"{name}-E{i}",
                first_name="Test",
                last_name=f"Person{i}",
                country_id=country_id,
                employment_status_id=status_id,
                department_id=dept.id,
                job_title_id=title_rows[0].id if title_rows else None,
            )
        )
    db.commit()
    return dept.id


def test_delete_department_blocked_when_employees_attached(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Department

    db = get_session_factory()()
    # Employee assignment needs a job title (FK for job_title_id NOT NULL).
    dept_id = _make_dept(db, "Blockable", titles=["Analyst"], employees=2)

    resp = client.post(
        f"/ui/lookups/departments/{dept_id}/delete", follow_redirects=False
    )
    assert resp.status_code == 303  # redirected with a flash error, not deleted
    db2 = get_session_factory()()
    assert db2.get(Department, dept_id) is not None


def test_delete_department_with_titles_prompts_then_cascades(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Department, JobTitle

    db = get_session_factory()()
    dept_id = _make_dept(db, "Cascade Co", titles=["T1", "T2", "T3"])

    # First POST (no confirm) → confirmation page listing the titles.
    resp = client.post(
        f"/ui/lookups/departments/{dept_id}/delete", follow_redirects=False
    )
    assert resp.status_code == 200
    assert "will be deleted" in resp.text
    assert "T2" in resp.text
    # Nothing deleted yet.
    db2 = get_session_factory()()
    assert db2.get(Department, dept_id) is not None
    assert db2.query(JobTitle).filter(JobTitle.department_id == dept_id).count() == 3

    # Confirmed POST → department + its titles are gone.
    resp = client.post(
        f"/ui/lookups/departments/{dept_id}/delete",
        data={"confirm": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db3 = get_session_factory()()
    assert db3.get(Department, dept_id) is None
    assert db3.query(JobTitle).filter(JobTitle.department_id == dept_id).count() == 0


def test_delete_empty_department_directly(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Department

    db = get_session_factory()()
    dept_id = _make_dept(db, "Empty Dept")

    resp = client.post(
        f"/ui/lookups/departments/{dept_id}/delete", follow_redirects=False
    )
    assert resp.status_code == 303
    db2 = get_session_factory()()
    assert db2.get(Department, dept_id) is None
