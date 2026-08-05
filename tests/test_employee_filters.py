"""Tests for sorting, filtering, and global search on the employees list UI.

The list page renders server-side, so we drive GET /ui/employees with query
params and assert on which employee numbers appear (and in what order) in the
rendered HTML.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db import get_session_factory
from app.models import (
    AppUser,
    Country,
    Department,
    Employee,
    EmploymentStatus,
    JobTitle,
    Location,
)
from app.services.passwords import hash_password

PASSWORD = "verysecure123"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    from app.config import get_settings

    settings = get_settings()
    resp = client.post(
        "/ui/login",
        data={
            "username": settings.initial_admin_username,
            "password": settings.initial_admin_password,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


def _refs() -> dict:
    """Grab a couple of distinct departments/statuses/etc. from seeded data."""
    S = get_session_factory()
    with S() as db:
        depts = db.query(Department).order_by(Department.id).all()
        statuses = db.query(EmploymentStatus).order_by(EmploymentStatus.id).all()
        active = next(s for s in statuses if s.is_active_status)
        inactive = next(s for s in statuses if not s.is_active_status)
        country = db.query(Country).order_by(Country.id).first()
        location = db.query(Location).order_by(Location.id).first()

        def title_for(dept_id: int) -> int:
            t = db.query(JobTitle).filter(JobTitle.department_id == dept_id).first()
            return t.id

        return {
            "dept_a": depts[0].id,
            "dept_b": depts[1].id,
            "dept_a_name": depts[0].name,
            "dept_b_name": depts[1].name,
            "title_a": title_for(depts[0].id),
            "title_b": title_for(depts[1].id),
            "active": active.id,
            "inactive": inactive.id,
            "country": country.id,
            "location": location.id if location else None,
        }


def _add(
    number: str,
    first: str,
    last: str,
    *,
    department_id: int,
    job_title_id: int,
    status_id: int,
    country_id: int,
    hire: date = date(2020, 1, 1),
    work_email: str | None = None,
    location_id: int | None = None,
    supervisor_id: int | None = None,
) -> int:
    S = get_session_factory()
    with S() as db:
        e = Employee(
            employee_number=number,
            first_name=first,
            last_name=last,
            department_id=department_id,
            job_title_id=job_title_id,
            employment_status_id=status_id,
            country_id=country_id,
            location_id=location_id,
            supervisor_id=supervisor_id,
            hire_date=hire,
            work_email=work_email,
        )
        db.add(e)
        db.commit()
        return e.id


def _numbers(html: str) -> list[str]:
    """Employee numbers, in row order, from the first (mono) cell of each row."""
    return re.findall(r'<td class="mono">([^<]+)</td>', html)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_blank_filter_params_do_not_error(admin_client: TestClient) -> None:
    """Unselected dropdowns submit empty strings; that must not 422/500."""
    resp = admin_client.get(
        "/ui/employees",
        params={
            "f_status": "",
            "f_department": "",
            "f_job_title": "",
            "f_supervisor": "",
            "f_country": "",
            "f_location": "",
            "f_employee_number": "",
            "f_name": "",
            "f_work_email": "",
            "f_hire_from": "",
            "f_hire_to": "",
            "q": "",
        },
    )
    assert resp.status_code == 200


def test_garbage_int_and_date_filters_are_ignored(admin_client: TestClient) -> None:
    resp = admin_client.get(
        "/ui/employees",
        params={"f_department": "abc", "f_hire_from": "not-a-date"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------


def test_global_search_matches_name(admin_client: TestClient) -> None:
    r = _refs()
    _add("FLT-AAA", "Zebediah", "Quennell", department_id=r["dept_a"],
         job_title_id=r["title_a"], status_id=r["active"], country_id=r["country"])
    _add("FLT-BBB", "Marcus", "Ordinary", department_id=r["dept_a"],
         job_title_id=r["title_a"], status_id=r["active"], country_id=r["country"])

    html = admin_client.get("/ui/employees", params={"view": "all", "q": "quennell"}).text
    nums = _numbers(html)
    assert "FLT-AAA" in nums
    assert "FLT-BBB" not in nums


def test_global_search_matches_employee_number_and_email(admin_client: TestClient) -> None:
    r = _refs()
    _add("UNIQNUM9", "Pat", "Smith", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"], work_email="findme@corp.example")
    _add("OTHER1", "Sam", "Jones", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"], work_email="nope@corp.example")

    by_num = _numbers(admin_client.get("/ui/employees", params={"view": "all", "q": "UNIQNUM9"}).text)
    assert by_num == ["UNIQNUM9"]

    by_email = _numbers(admin_client.get("/ui/employees", params={"view": "all", "q": "findme@corp"}).text)
    assert "UNIQNUM9" in by_email
    assert "OTHER1" not in by_email


# ---------------------------------------------------------------------------
# Per-column filters
# ---------------------------------------------------------------------------


def test_column_text_filter_employee_number(admin_client: TestClient) -> None:
    r = _refs()
    _add("ABC-100", "A", "One", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])
    _add("XYZ-200", "B", "Two", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])

    nums = _numbers(admin_client.get("/ui/employees", params={"view": "all", "f_employee_number": "abc"}).text)
    assert "ABC-100" in nums
    assert "XYZ-200" not in nums


def test_column_filter_department(admin_client: TestClient) -> None:
    r = _refs()
    _add("DEP-A1", "In", "DeptA", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])
    _add("DEP-B1", "In", "DeptB", department_id=r["dept_b"], job_title_id=r["title_b"],
         status_id=r["active"], country_id=r["country"])

    nums = _numbers(admin_client.get(
        "/ui/employees", params={"view": "all", "f_department": str(r["dept_a"])}).text)
    assert "DEP-A1" in nums
    assert "DEP-B1" not in nums


def test_column_filter_status(admin_client: TestClient) -> None:
    r = _refs()
    _add("ST-ACT", "Act", "Ive", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])
    _add("ST-INA", "In", "Active", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["inactive"], country_id=r["country"])

    nums = _numbers(admin_client.get(
        "/ui/employees", params={"view": "all", "f_status": str(r["inactive"])}).text)
    assert "ST-INA" in nums
    assert "ST-ACT" not in nums


def test_hire_date_range_filter(admin_client: TestClient) -> None:
    r = _refs()
    _add("HIRE-OLD", "Old", "Timer", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"], hire=date(2010, 6, 1))
    _add("HIRE-NEW", "New", "Hire", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"], hire=date(2024, 6, 1))

    # From 2020 onward → only the new hire.
    nums = _numbers(admin_client.get(
        "/ui/employees", params={"view": "all", "f_hire_from": "2020-01-01"}).text)
    assert "HIRE-NEW" in nums
    assert "HIRE-OLD" not in nums

    # Up to 2015 → only the old timer.
    nums = _numbers(admin_client.get(
        "/ui/employees", params={"view": "all", "f_hire_to": "2015-01-01"}).text)
    assert "HIRE-OLD" in nums
    assert "HIRE-NEW" not in nums


def test_supervisor_filter(admin_client: TestClient) -> None:
    r = _refs()
    boss = _add("BOSS-1", "The", "Boss", department_id=r["dept_a"], job_title_id=r["title_a"],
                status_id=r["active"], country_id=r["country"])
    _add("REPORT-1", "A", "Report", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"], supervisor_id=boss)
    _add("NOBODY-1", "No", "Boss", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])

    nums = _numbers(admin_client.get(
        "/ui/employees", params={"view": "all", "f_supervisor": str(boss)}).text)
    assert "REPORT-1" in nums
    assert "NOBODY-1" not in nums


def test_filters_combine(admin_client: TestClient) -> None:
    r = _refs()
    _add("COMBO-1", "Match", "Both", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])
    _add("COMBO-2", "Match", "DeptOnly", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["inactive"], country_id=r["country"])

    nums = _numbers(admin_client.get("/ui/employees", params={
        "view": "all", "f_department": str(r["dept_a"]), "f_status": str(r["active"]),
        "f_name": "Both",
    }).text)
    assert "COMBO-1" in nums
    assert "COMBO-2" not in nums


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_sort_by_department_asc_and_desc(admin_client: TestClient) -> None:
    r = _refs()
    # Two employees, same active status so active-first grouping doesn't split
    # them; different departments so the sort column decides the order.
    _add("SORT-A", "x", "x", department_id=r["dept_a"], job_title_id=r["title_a"],
         status_id=r["active"], country_id=r["country"])
    _add("SORT-B", "x", "x", department_id=r["dept_b"], job_title_id=r["title_b"],
         status_id=r["active"], country_id=r["country"])
    # dept_a sorts before dept_b by name? Determine expected order from names.
    a_first = r["dept_a_name"] < r["dept_b_name"]

    asc = _numbers(admin_client.get("/ui/employees", params={
        "view": "all", "f_name": "x", "sort": "department", "order": "asc"}).text)
    desc = _numbers(admin_client.get("/ui/employees", params={
        "view": "all", "f_name": "x", "sort": "department", "order": "desc"}).text)

    assert set(asc) == {"SORT-A", "SORT-B"}
    assert asc == list(reversed(desc))
    if a_first:
        assert asc.index("SORT-A") < asc.index("SORT-B")
    else:
        assert asc.index("SORT-B") < asc.index("SORT-A")


def test_all_sort_keys_return_200(admin_client: TestClient) -> None:
    keys = [
        "employee_number", "last_name", "status", "department", "job_title",
        "work_email", "supervisor", "hire_date", "country", "location",
    ]
    for key in keys:
        for order in ("asc", "desc"):
            resp = admin_client.get(
                "/ui/employees", params={"view": "all", "sort": key, "order": order})
            assert resp.status_code == 200, f"{key}/{order} failed"


def test_invalid_sort_falls_back(admin_client: TestClient) -> None:
    resp = admin_client.get("/ui/employees", params={"view": "all", "sort": "bogus"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# State preservation in links
# ---------------------------------------------------------------------------


def test_active_filter_shows_clear_button_and_preserves_in_sort_links(
    admin_client: TestClient,
) -> None:
    r = _refs()
    html = admin_client.get(
        "/ui/employees", params={"view": "all", "f_department": str(r["dept_a"])}).text
    # Clear-filters affordance appears when a filter is active.
    assert "Clear filters" in html
    # Sort-header links carry the active filter so sorting doesn't drop it.
    assert f"f_department={r['dept_a']}" in html


def test_no_clear_button_without_filters(admin_client: TestClient) -> None:
    html = admin_client.get("/ui/employees", params={"view": "all"}).text
    assert "Clear filters" not in html


# ---------------------------------------------------------------------------
# Authorization: read-only users can still sort/filter
# ---------------------------------------------------------------------------


def test_view_only_user_can_filter(client: TestClient) -> None:
    S = get_session_factory()
    with S() as db:
        db.add(AppUser(username="vo", password_hash=hash_password(PASSWORD),
                       role="view_only", is_active=True, is_seeded=False))
        db.commit()
    resp = client.post("/ui/login", data={"username": "vo", "password": PASSWORD},
                       follow_redirects=False)
    assert resp.status_code == 303
    r = _refs()
    resp = client.get("/ui/employees", params={"view": "all", "f_department": str(r["dept_a"]),
                                               "sort": "department"})
    assert resp.status_code == 200
