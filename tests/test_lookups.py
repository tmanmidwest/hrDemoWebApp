"""Tests for lookup-table CRUD endpoints (countries, states, statuses, depts, titles)."""

from __future__ import annotations

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Auth requirement (spot check — all lookup endpoints use the same dependency)
# ---------------------------------------------------------------------------


def test_lookup_endpoints_require_auth(client: TestClient) -> None:
    """Without an Authorization header, every lookup GET returns 401."""
    for path in [
        "/api/v1/countries/",
        "/api/v1/states-provinces/",
        "/api/v1/employment-statuses/",
        "/api/v1/departments/",
        "/api/v1/job-titles/",
        "/api/v1/locations/",
    ]:
        resp = client.get(path)
        assert resp.status_code == 401, f"{path} did not require auth"


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------


def test_list_countries_returns_seeded_data(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/countries/")
    assert resp.status_code == 200
    countries = resp.json()
    assert len(countries) >= 50
    codes = {c["code"] for c in countries}
    assert "US" in codes
    assert "CA" in codes


def test_create_country(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/countries/", json={"code": "zz", "name": "Atlantis"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "ZZ"  # Normalized to uppercase
    assert body["name"] == "Atlantis"


def test_create_country_duplicate_code_returns_409(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/countries/", json={"code": "US", "name": "Dup"})
    assert resp.status_code == 409


def test_update_country(api_client: TestClient) -> None:
    # Find a country we can safely update — pick one that isn't US
    list_resp = api_client.get("/api/v1/countries/")
    candidate = next(c for c in list_resp.json() if c["code"] == "IS")
    resp = api_client.patch(
        f"/api/v1/countries/{candidate['id']}", json={"name": "Iceland (test)"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Iceland (test)"


def test_delete_country_referenced_by_employee_returns_409(
    api_client: TestClient,
) -> None:
    """The sample employee references 'United States'. Cannot delete US."""
    list_resp = api_client.get("/api/v1/countries/")
    us = next(c for c in list_resp.json() if c["code"] == "US")
    resp = api_client.delete(f"/api/v1/countries/{us['id']}")
    assert resp.status_code == 409
    assert "employees" in resp.json()["detail"]


def test_delete_country_with_no_references(api_client: TestClient) -> None:
    # Create a new unreferenced country, then delete it
    create_resp = api_client.post(
        "/api/v1/countries/", json={"code": "XQ", "name": "Test Country"}
    )
    new_id = create_resp.json()["id"]
    del_resp = api_client.delete(f"/api/v1/countries/{new_id}")
    assert del_resp.status_code == 204
    # Confirm it's gone
    get_resp = api_client.get(f"/api/v1/countries/{new_id}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# States / Provinces
# ---------------------------------------------------------------------------


def test_list_states_filtered_by_country(api_client: TestClient) -> None:
    countries = api_client.get("/api/v1/countries/").json()
    us = next(c for c in countries if c["code"] == "US")

    resp = api_client.get(f"/api/v1/states-provinces/?country_id={us['id']}")
    assert resp.status_code == 200
    states = resp.json()
    assert len(states) == 51  # 50 states + DC
    for state in states:
        assert state["country_id"] == us["id"]


def test_create_state_for_country(api_client: TestClient) -> None:
    countries = api_client.get("/api/v1/countries/").json()
    fr = next(c for c in countries if c["code"] == "FR")
    resp = api_client.post(
        "/api/v1/states-provinces/",
        json={"country_id": fr["id"], "code": "FR-75", "name": "Île-de-France"},
    )
    assert resp.status_code == 201
    assert resp.json()["country_id"] == fr["id"]


def test_create_state_with_invalid_country_returns_400(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/states-provinces/",
        json={"country_id": 99999, "name": "Nowhere"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Employment Statuses
# ---------------------------------------------------------------------------


def test_list_employment_statuses(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/employment-statuses/")
    assert resp.status_code == 200
    statuses = resp.json()
    labels = {s["label"] for s in statuses}
    assert {"Active", "Not Active", "Leave of Absence", "Terminated"}.issubset(labels)


def test_create_custom_employment_status(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/employment-statuses/",
        json={"label": "Suspended", "value": 4, "is_active_status": False},
    )
    assert resp.status_code == 201
    assert resp.json()["is_system"] is False


def test_cannot_delete_system_status(api_client: TestClient) -> None:
    statuses = api_client.get("/api/v1/employment-statuses/").json()
    active = next(s for s in statuses if s["label"] == "Active")
    resp = api_client.delete(f"/api/v1/employment-statuses/{active['id']}")
    assert resp.status_code == 409
    assert "system" in resp.json()["detail"].lower()


def test_cannot_change_system_status_value(api_client: TestClient) -> None:
    statuses = api_client.get("/api/v1/employment-statuses/").json()
    active = next(s for s in statuses if s["label"] == "Active")
    resp = api_client.patch(
        f"/api/v1/employment-statuses/{active['id']}", json={"value": 99}
    )
    assert resp.status_code == 409


def test_can_rename_system_status_but_not_change_value(api_client: TestClient) -> None:
    statuses = api_client.get("/api/v1/employment-statuses/").json()
    active = next(s for s in statuses if s["label"] == "Active")
    # Renaming is allowed
    resp = api_client.patch(
        f"/api/v1/employment-statuses/{active['id']}", json={"label": "Currently Active"}
    )
    assert resp.status_code == 200


def test_delete_employment_status_referenced_returns_409(api_client: TestClient) -> None:
    """The 'Not Active' status is referenced by sample employees and is_system=True.

    We expect 409 either way. Test the referenced (non-system) status route:
    create a custom status, reference it from a new employee, try to delete.
    Simpler: just verify the referenced 'Not Active' (system) status is blocked.
    """
    statuses = api_client.get("/api/v1/employment-statuses/").json()
    not_active = next(s for s in statuses if s["label"] == "Not Active")
    resp = api_client.delete(f"/api/v1/employment-statuses/{not_active['id']}")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def test_list_departments(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/departments/")
    assert resp.status_code == 200
    names = {d["name"] for d in resp.json()}
    assert "Engineering" in names


def test_create_department(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/departments/", json={"name": "Legal"})
    assert resp.status_code == 201


def test_delete_referenced_department_returns_409(api_client: TestClient) -> None:
    depts = api_client.get("/api/v1/departments/").json()
    eng = next(d for d in depts if d["name"] == "Engineering")
    resp = api_client.delete(f"/api/v1/departments/{eng['id']}")
    assert resp.status_code == 409
    # Engineering has both employees AND job titles referencing it
    detail = resp.json()["detail"]
    assert "employees" in detail and "job titles" in detail


# ---------------------------------------------------------------------------
# Job titles
# ---------------------------------------------------------------------------


def test_list_job_titles_filtered_by_department(api_client: TestClient) -> None:
    depts = api_client.get("/api/v1/departments/").json()
    eng = next(d for d in depts if d["name"] == "Engineering")
    resp = api_client.get(f"/api/v1/job-titles/?department_id={eng['id']}")
    assert resp.status_code == 200
    titles = resp.json()
    assert len(titles) >= 3
    for t in titles:
        assert t["department_id"] == eng["id"]


def test_create_job_title_with_invalid_department(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/job-titles/", json={"department_id": 99999, "name": "Nobody"}
    )
    assert resp.status_code == 400


def test_delete_referenced_job_title_returns_409(api_client: TestClient) -> None:
    titles = api_client.get("/api/v1/job-titles/").json()
    # Find one referenced by a sample employee — Software Engineer or Senior SWE
    ref = next(
        t for t in titles
        if t["name"] in ("Software Engineer", "Senior Software Engineer")
    )
    resp = api_client.delete(f"/api/v1/job-titles/{ref['id']}")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def test_list_locations_returns_seeded_data(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/locations/")
    assert resp.status_code == 200
    locations = resp.json()
    names = {loc["name"] for loc in locations}
    assert "Chicago HQ" in names
    assert len(locations) >= 8


def test_create_location(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/locations/", json={"name": "Berlin Office"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Berlin Office"
    assert body["is_active"] is True


def test_create_location_duplicate_name_returns_409(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/locations/", json={"name": "Chicago HQ"})
    assert resp.status_code == 409


def test_update_location(api_client: TestClient) -> None:
    list_resp = api_client.get("/api/v1/locations/")
    candidate = next(loc for loc in list_resp.json() if loc["name"] == "Austin Office")
    resp = api_client.patch(
        f"/api/v1/locations/{candidate['id']}",
        json={"name": "Austin HQ", "is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Austin HQ"
    assert resp.json()["is_active"] is False


def test_filter_locations_by_is_active(api_client: TestClient) -> None:
    # Deactivate one
    list_resp = api_client.get("/api/v1/locations/")
    target = next(loc for loc in list_resp.json() if loc["name"] == "London Office")
    api_client.patch(f"/api/v1/locations/{target['id']}", json={"is_active": False})

    active = api_client.get("/api/v1/locations/?is_active=true").json()
    inactive = api_client.get("/api/v1/locations/?is_active=false").json()
    active_names = {loc["name"] for loc in active}
    inactive_names = {loc["name"] for loc in inactive}
    assert "London Office" not in active_names
    assert "London Office" in inactive_names


def test_delete_location_with_no_references(api_client: TestClient) -> None:
    create_resp = api_client.post(
        "/api/v1/locations/", json={"name": "Throwaway Office"}
    )
    loc_id = create_resp.json()["id"]
    resp = api_client.delete(f"/api/v1/locations/{loc_id}")
    assert resp.status_code == 204
    assert api_client.get(f"/api/v1/locations/{loc_id}").status_code == 404


def test_get_location_not_found(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/locations/999999").status_code == 404
