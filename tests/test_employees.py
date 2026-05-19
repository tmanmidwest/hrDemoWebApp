"""Tests for the employee REST API.

Includes:
- Basic CRUD
- Cross-FK validation (state belongs to country, job title belongs to department,
  supervisor must be active and not the same employee)
- Archive / restore lifecycle
- Saviynt-friendly filters (include_archived, updated_since, eligible_supervisor)
- Sort order (active employees first per spec)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helper: build a minimal valid employee payload
# ---------------------------------------------------------------------------


@pytest.fixture
def lookup_ids(api_client: TestClient) -> dict[str, int]:
    """Return a dict of useful seeded IDs for building employee payloads."""
    countries = api_client.get("/api/v1/countries/").json()
    statuses = api_client.get("/api/v1/employment-statuses/").json()
    depts = api_client.get("/api/v1/departments/").json()
    titles = api_client.get("/api/v1/job-titles/").json()
    states = api_client.get("/api/v1/states-provinces/").json()

    eng = next(d for d in depts if d["name"] == "Engineering")
    eng_title = next(
        t for t in titles
        if t["department_id"] == eng["id"] and t["name"] == "Software Engineer"
    )
    sales = next(d for d in depts if d["name"] == "Sales")
    sales_title = next(
        t for t in titles if t["department_id"] == sales["id"]
    )
    us = next(c for c in countries if c["code"] == "US")
    illinois = next(s for s in states if s["name"] == "Illinois" and s["country_id"] == us["id"])
    ontario = next(s for s in states if s["name"] == "Ontario")

    return {
        "us_id": us["id"],
        "canada_id": next(c for c in countries if c["code"] == "CA")["id"],
        "illinois_id": illinois["id"],
        "ontario_id": ontario["id"],
        "active_status_id": next(s for s in statuses if s["label"] == "Active")["id"],
        "not_active_status_id": next(
            s for s in statuses if s["label"] == "Not Active"
        )["id"],
        "terminated_status_id": next(
            s for s in statuses if s["label"] == "Terminated"
        )["id"],
        "engineering_id": eng["id"],
        "sales_id": sales["id"],
        "eng_swe_title_id": eng_title["id"],
        "sales_title_id": sales_title["id"],
    }


@pytest.fixture
def first_existing_supervisor_id(api_client: TestClient) -> int:
    """Get the ID of one of the seeded sample employees we can use as supervisor.

    The seeded data has 'Sample Manager' (no supervisor) and 'Sample Employee'
    (supervised by Sample Manager). But both are seeded with 'Not Active' status,
    so we need to activate Sample Manager first before we can use them.
    """
    # Activate the seeded 'Sample Manager'
    list_resp = api_client.get("/api/v1/employees/?include_archived=true")
    employees = list_resp.json()
    sample_manager = next(e for e in employees if e["employee_number"] == "E00001")

    statuses = api_client.get("/api/v1/employment-statuses/").json()
    active_id = next(s["id"] for s in statuses if s["label"] == "Active")

    api_client.patch(
        f"/api/v1/employees/{sample_manager['id']}",
        json={"employment_status_id": active_id},
    )
    return int(sample_manager["id"])


def _minimal_payload(
    lookup_ids: dict[str, int],
    supervisor_id: int | None,
    *,
    employee_number: str = "E10001",
    first_name: str = "Jane",
    last_name: str = "Doe",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "employee_number": employee_number,
        "first_name": first_name,
        "last_name": last_name,
        "country_id": lookup_ids["us_id"],
        "employment_status_id": lookup_ids["active_status_id"],
        "department_id": lookup_ids["engineering_id"],
        "job_title_id": lookup_ids["eng_swe_title_id"],
        "hire_date": "2026-01-15",
    }
    if supervisor_id is not None:
        payload["supervisor_id"] = supervisor_id
    return payload


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_employee_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/employees/").status_code == 401
    assert client.get("/api/v1/employees/1").status_code == 401
    assert client.post("/api/v1/employees/", json={}).status_code == 401


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


def test_list_employees_hides_archived_by_default(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/employees/")
    assert resp.status_code == 200
    for e in resp.json():
        assert e["is_archived"] is False


def test_list_employees_include_archived(api_client: TestClient) -> None:
    # Initial count
    initial = api_client.get("/api/v1/employees/").json()

    # Archive one
    archive_resp = api_client.post(
        f"/api/v1/employees/{initial[0]['id']}/archive"
    )
    assert archive_resp.status_code == 200

    # Default list now has one fewer
    after = api_client.get("/api/v1/employees/").json()
    assert len(after) == len(initial) - 1

    # With include_archived, original count restored
    with_archived = api_client.get("/api/v1/employees/?include_archived=true").json()
    assert len(with_archived) == len(initial)


def test_list_employees_archived_only(api_client: TestClient) -> None:
    initial = api_client.get("/api/v1/employees/").json()
    api_client.post(f"/api/v1/employees/{initial[0]['id']}/archive")

    only_archived = api_client.get("/api/v1/employees/?archived_only=true").json()
    assert len(only_archived) == 1
    assert only_archived[0]["is_archived"] is True


def test_employee_response_includes_nested_objects(api_client: TestClient) -> None:
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    e = employees[0]
    assert "country" in e and "code" in e["country"]
    assert "department" in e and "name" in e["department"]
    assert "job_title" in e and "name" in e["job_title"]
    assert "employment_status" in e and "is_active_status" in e["employment_status"]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_employee_with_valid_data(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    resp = api_client.post(
        "/api/v1/employees/",
        json=_minimal_payload(lookup_ids, first_existing_supervisor_id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["employee_number"] == "E10001"
    assert body["supervisor"]["id"] == first_existing_supervisor_id


def test_create_employee_duplicate_number_returns_409(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    api_client.post(
        "/api/v1/employees/",
        json=_minimal_payload(lookup_ids, first_existing_supervisor_id),
    )
    resp2 = api_client.post(
        "/api/v1/employees/",
        json=_minimal_payload(lookup_ids, first_existing_supervisor_id),
    )
    assert resp2.status_code == 409


def test_create_employee_missing_supervisor_when_employees_exist(
    api_client: TestClient, lookup_ids: dict[str, int]
) -> None:
    """Seeded data has employees, so a new employee MUST have a supervisor."""
    payload = _minimal_payload(lookup_ids, supervisor_id=None)
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 400
    assert "supervisor_id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Cross-FK validation
# ---------------------------------------------------------------------------


def test_create_employee_state_must_belong_to_country(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    """Ontario (CA) cannot be used with country US."""
    payload = _minimal_payload(lookup_ids, first_existing_supervisor_id)
    payload["country_id"] = lookup_ids["us_id"]
    payload["state_province_id"] = lookup_ids["ontario_id"]  # Ontario is CA
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 400
    assert "country" in resp.json()["detail"].lower()


def test_create_employee_job_title_must_belong_to_department(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    """Sales title cannot be used with Engineering department."""
    payload = _minimal_payload(lookup_ids, first_existing_supervisor_id)
    payload["department_id"] = lookup_ids["engineering_id"]
    payload["job_title_id"] = lookup_ids["sales_title_id"]
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 400
    assert "department" in resp.json()["detail"].lower()


def test_create_employee_termination_before_hire_rejected(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    payload = _minimal_payload(lookup_ids, first_existing_supervisor_id)
    payload["hire_date"] = "2026-06-01"
    payload["termination_date"] = "2025-01-01"
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 422  # Pydantic validation


def test_create_employee_supervisor_must_be_active(
    api_client: TestClient,
    lookup_ids: dict[str, int],
) -> None:
    """A 'Not Active' employee cannot be assigned as a supervisor.

    The seeded 'Sample Manager' is in Not Active state by default — perfect.
    """
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    inactive_emp = next(e for e in employees if e["employee_number"] == "E00001")
    assert inactive_emp["employment_status"]["label"] == "Not Active"

    payload = _minimal_payload(lookup_ids, supervisor_id=inactive_emp["id"])
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 400
    assert "active" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_employee_basic(api_client: TestClient) -> None:
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    e = employees[0]
    resp = api_client.patch(
        f"/api/v1/employees/{e['id']}",
        json={"first_name": "Renamed", "city": "Chicago"},
    )
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "Renamed"
    assert resp.json()["city"] == "Chicago"


def test_update_employee_cannot_become_own_supervisor(api_client: TestClient) -> None:
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    e = employees[0]
    resp = api_client.patch(
        f"/api/v1/employees/{e['id']}", json={"supervisor_id": e["id"]}
    )
    assert resp.status_code == 400
    assert "own supervisor" in resp.json()["detail"]


def test_update_employee_cross_country_state_consistency(
    api_client: TestClient,
    lookup_ids: dict[str, int],
) -> None:
    """If we change just the country, an existing state belonging to the old
    country becomes invalid and should be rejected.
    """
    # Seed employee with US/Illinois
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    e = employees[0]
    api_client.patch(
        f"/api/v1/employees/{e['id']}",
        json={
            "country_id": lookup_ids["us_id"],
            "state_province_id": lookup_ids["illinois_id"],
        },
    )

    # Now try to change country to Canada (Illinois isn't a CA state)
    resp = api_client.patch(
        f"/api/v1/employees/{e['id']}",
        json={"country_id": lookup_ids["canada_id"]},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Archive / Restore
# ---------------------------------------------------------------------------


def test_archive_and_restore_employee(api_client: TestClient) -> None:
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    e = employees[0]

    # Archive
    arch = api_client.post(f"/api/v1/employees/{e['id']}/archive")
    assert arch.status_code == 200
    assert arch.json()["is_archived"] is True
    assert arch.json()["archived_at"] is not None

    # Restore
    rest = api_client.post(f"/api/v1/employees/{e['id']}/restore")
    assert rest.status_code == 200
    assert rest.json()["is_archived"] is False
    assert rest.json()["archived_at"] is None


def test_archived_employee_cannot_be_supervisor(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    # Archive the supervisor candidate
    api_client.post(f"/api/v1/employees/{first_existing_supervisor_id}/archive")

    payload = _minimal_payload(lookup_ids, supervisor_id=first_existing_supervisor_id)
    resp = api_client.post("/api/v1/employees/", json=payload)
    assert resp.status_code == 400
    assert "archived" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Incremental sync (Saviynt's use case)
# ---------------------------------------------------------------------------


def test_updated_since_filter(api_client: TestClient) -> None:
    # Take a snapshot timestamp BEFORE any modifications
    employees = api_client.get("/api/v1/employees/?include_archived=true").json()
    e_to_update = employees[0]

    # Wait a moment, modify one, then filter
    import time

    snapshot_time = datetime.now(UTC)
    time.sleep(0.1)  # Ensure updated_at strictly after snapshot_time

    api_client.patch(
        f"/api/v1/employees/{e_to_update['id']}", json={"city": "Recent Update"}
    )

    # Pass the datetime via params= so httpx URL-encodes it properly
    filtered_resp = api_client.get(
        "/api/v1/employees/",
        params={
            "updated_since": snapshot_time.isoformat(),
            "include_archived": "true",
        },
    )
    assert filtered_resp.status_code == 200, filtered_resp.text
    filtered = filtered_resp.json()
    assert len(filtered) == 1
    assert filtered[0]["id"] == e_to_update["id"]


# ---------------------------------------------------------------------------
# Eligible supervisor mode
# ---------------------------------------------------------------------------


def test_eligible_supervisor_filter(
    api_client: TestClient,
    first_existing_supervisor_id: int,
) -> None:
    """eligible_supervisor=true should return only employees with active status
    and not archived.
    """
    # first_existing_supervisor_id fixture has activated 'Sample Manager'
    resp = api_client.get("/api/v1/employees/?eligible_supervisor=true")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert first_existing_supervisor_id in ids
    # All returned employees must satisfy the constraints
    for e in resp.json():
        assert e["is_archived"] is False
        assert e["employment_status"]["is_active_status"] is True


def test_eligible_supervisor_with_exclude_id(
    api_client: TestClient,
    first_existing_supervisor_id: int,
) -> None:
    resp = api_client.get(
        f"/api/v1/employees/?eligible_supervisor=true&exclude_id={first_existing_supervisor_id}"
    )
    ids = [e["id"] for e in resp.json()]
    assert first_existing_supervisor_id not in ids


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_default_sort_puts_active_status_first(
    api_client: TestClient,
    lookup_ids: dict[str, int],
    first_existing_supervisor_id: int,
) -> None:
    """Per spec: 'always display Active employees first and inactive employees last.'"""
    # Create an active employee
    api_client.post(
        "/api/v1/employees/",
        json=_minimal_payload(
            lookup_ids,
            first_existing_supervisor_id,
            employee_number="ACTIVE_NEW",
            last_name="ZZZ_active_should_be_first",
        ),
    )

    # The seeded Sample Employee is 'Not Active'.
    # Default listing should show active employees before non-active.
    resp = api_client.get("/api/v1/employees/?include_archived=false")
    employees = resp.json()
    seen_inactive = False
    for e in employees:
        if e["employment_status"]["is_active_status"]:
            assert not seen_inactive, (
                "An active employee appeared AFTER a non-active one — "
                "default sort is broken"
            )
        else:
            seen_inactive = True


def test_invalid_sort_field_rejected(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/employees/?sort=nonsense_column")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# OAuth-authenticated calls work too
# ---------------------------------------------------------------------------


def test_employee_list_works_with_oauth_jwt(
    client: TestClient, admin_session: TestClient
) -> None:
    """Sanity check that a JWT from /oauth/token works on these endpoints."""
    # Create OAuth client via session
    create_resp = admin_session.post(
        "/api/v1/auth/oauth-clients/", json={"name": "Test OAuth"}
    )
    creds = create_resp.json()

    # Get token
    token_resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        },
    )
    assert token_resp.status_code == 200
    jwt = token_resp.json()["access_token"]

    # Call employees endpoint with JWT
    resp = client.get(
        "/api/v1/employees/", headers={"Authorization": f"Bearer {jwt}"}
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 0
