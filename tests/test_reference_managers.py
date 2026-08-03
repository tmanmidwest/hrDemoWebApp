"""Tests for static reference managers.

A reference manager (e.g. ``margaretmanager``) is a stand-in supervisor record
that is hidden from the API/MCP employee list, CSV export, and reports, but is
still assignable as a supervisor, fetchable by id, and shown (badged) in the UI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def lookup_ids(api_client: TestClient) -> dict[str, int]:
    countries = api_client.get("/api/v1/countries/").json()
    statuses = api_client.get("/api/v1/employment-statuses/").json()
    depts = api_client.get("/api/v1/departments/").json()
    titles = api_client.get("/api/v1/job-titles/").json()
    eng = next(d for d in depts if d["name"] == "Engineering")
    eng_title = next(
        t for t in titles
        if t["department_id"] == eng["id"] and t["name"] == "Software Engineer"
    )
    us = next(c for c in countries if c["code"] == "US")
    return {
        "us_id": us["id"],
        "active_status_id": next(s for s in statuses if s["label"] == "Active")["id"],
        "engineering_id": eng["id"],
        "eng_swe_title_id": eng_title["id"],
    }


def _seed_margaret() -> int:
    """Create the seeded reference manager and return her id."""
    from app.db import get_session_factory
    from app.services import reference_managers

    with get_session_factory()() as db:
        mgr, _ = reference_managers.ensure_seed_manager(db)
        assert mgr is not None
        return int(mgr.id)


def _make_employee(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    *,
    employee_number: str,
    supervisor_id: int,
) -> dict:
    payload: dict[str, object] = {
        "employee_number": employee_number,
        "first_name": "Test",
        "last_name": "Person",
        "country_id": lookup_ids["us_id"],
        "employment_status_id": lookup_ids["active_status_id"],
        "department_id": lookup_ids["engineering_id"],
        "job_title_id": lookup_ids["eng_swe_title_id"],
        "hire_date": "2026-01-15",
        "supervisor_id": supervisor_id,
    }
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# API list exclusion
# ---------------------------------------------------------------------------


def test_reference_manager_hidden_from_list(api_client: TestClient) -> None:
    _seed_margaret()
    numbers = [e["employee_number"] for e in api_client.get("/api/v1/employees/").json()]
    assert "margaretmanager" not in numbers


def test_reference_manager_shown_when_included(api_client: TestClient) -> None:
    _seed_margaret()
    resp = api_client.get(
        "/api/v1/employees/?include_reference_managers=true&include_archived=true"
    ).json()
    flagged = next(e for e in resp if e["employee_number"] == "margaretmanager")
    assert flagged["is_reference_manager"] is True


def test_reference_manager_still_fetchable_by_id(api_client: TestClient) -> None:
    mgr_id = _seed_margaret()
    resp = api_client.get(f"/api/v1/employees/{mgr_id}")
    assert resp.status_code == 200
    assert resp.json()["is_reference_manager"] is True


def test_reference_manager_assignable_and_resolves_as_supervisor(
    api_client: TestClient, lookup_ids: dict[str, int]
) -> None:
    mgr_id = _seed_margaret()

    # Still offered as an eligible supervisor even though hidden from the roster.
    eligible = api_client.get("/api/v1/employees/?eligible_supervisor=true").json()
    assert mgr_id in [e["id"] for e in eligible]

    report = _make_employee(
        api_client, lookup_ids, employee_number="E20001", supervisor_id=mgr_id
    )
    # The report carries the manager handle with no spaces.
    assert report["supervisor"]["employee_number"] == "margaretmanager"
    assert report["supervisor"]["is_reference_manager"] is True


# ---------------------------------------------------------------------------
# CSV export exclusion
# ---------------------------------------------------------------------------


def test_reference_manager_excluded_from_csv_export(
    api_client: TestClient, admin_session: TestClient
) -> None:
    _seed_margaret()
    resp = admin_session.get("/ui/employees/export.csv")
    assert resp.status_code == 200
    assert "margaretmanager" not in resp.text


# ---------------------------------------------------------------------------
# Reports exclusion
# ---------------------------------------------------------------------------


def test_reference_manager_excluded_from_headcount(api_client: TestClient) -> None:
    before = api_client.get(
        "/api/v1/reports/headcount?group_by=department"
    ).json()["total"]
    _seed_margaret()
    after = api_client.get(
        "/api/v1/reports/headcount?group_by=department"
    ).json()["total"]
    assert after == before


def test_reference_manager_not_a_manager_node_in_org_report(
    api_client: TestClient, lookup_ids: dict[str, int]
) -> None:
    mgr_id = _seed_margaret()
    _make_employee(
        api_client, lookup_ids, employee_number="E20002", supervisor_id=mgr_id
    )
    org = api_client.get("/api/v1/reports/org").json()
    mgr_numbers = [m["employee_number"] for m in org["managers"]]
    assert "margaretmanager" not in mgr_numbers


# ---------------------------------------------------------------------------
# Seed helper + feature toggle
# ---------------------------------------------------------------------------


def test_ensure_seed_manager_is_idempotent_and_flagged(client: TestClient) -> None:
    from app.db import get_session_factory
    from app.services import reference_managers

    with get_session_factory()() as db:
        mgr, created = reference_managers.ensure_seed_manager(db)
        assert created is True
        assert mgr is not None
        assert mgr.employee_number == "margaretmanager"
        assert mgr.is_reference_manager is True
        assert mgr.work_email == "margaretmanager@saviynt.com"

        again, created_again = reference_managers.ensure_seed_manager(db)
        assert created_again is False
        assert again.id == mgr.id


def test_feature_toggle_round_trips(client: TestClient) -> None:
    from app.db import get_session_factory
    from app.services import reference_managers

    with get_session_factory()() as db:
        assert reference_managers.is_enabled(db) is False
        reference_managers.set_enabled(db, True)
        assert reference_managers.is_enabled(db) is True
        reference_managers.set_enabled(db, False)
        assert reference_managers.is_enabled(db) is False
