"""Department, job title, employment status, hire date, and supervisor are all
optional when creating/updating an employee — only number, name, and country
are required."""

from __future__ import annotations


def _country_id(client, headers) -> int:
    body = client.get("/api/v1/countries/?limit=1", headers=headers).json()
    items = body["items"] if isinstance(body, dict) else body
    return items[0]["id"]


def test_api_create_minimal_employee(api_key, admin_session):
    client = admin_session
    h = {"Authorization": f"Bearer {api_key}"}
    cid = _country_id(client, h)

    r = client.post(
        "/api/v1/employees/",
        headers=h,
        json={
            "employee_number": "MIN-1",
            "first_name": "Minimal",
            "last_name": "Person",
            "country_id": cid,
        },
    )
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["department"] is None
    assert e["job_title"] is None
    assert e["employment_status"] is None
    assert e["hire_date"] is None
    assert e["supervisor"] is None

    # Visible in the default listing (outer joins, not dropped).
    listed = client.get("/api/v1/employees/?limit=200", headers=h).json()
    assert any(x["employee_number"] == "MIN-1" for x in listed)


def test_api_missing_country_still_rejected(api_key, admin_session):
    client = admin_session
    h = {"Authorization": f"Bearer {api_key}"}
    r = client.post(
        "/api/v1/employees/",
        headers=h,
        json={"employee_number": "NOC-1", "first_name": "No", "last_name": "Country"},
    )
    assert r.status_code == 422  # country_id is still required


def test_api_bad_optional_fk_rejected(api_key, admin_session):
    client = admin_session
    h = {"Authorization": f"Bearer {api_key}"}
    cid = _country_id(client, h)
    r = client.post(
        "/api/v1/employees/",
        headers=h,
        json={
            "employee_number": "BAD-1",
            "first_name": "Bad",
            "last_name": "Dept",
            "country_id": cid,
            "department_id": 999999,
        },
    )
    assert r.status_code == 400  # provided-but-nonexistent FK is still validated


def test_api_update_can_clear_optional_fields(api_key, admin_session):
    client = admin_session
    h = {"Authorization": f"Bearer {api_key}"}
    cid = _country_id(client, h)
    created = client.post(
        "/api/v1/employees/",
        headers=h,
        json={
            "employee_number": "CLR-1",
            "first_name": "Clear",
            "last_name": "Me",
            "country_id": cid,
        },
    ).json()
    # Clearing already-null optional fields is a no-op success.
    r = client.patch(
        f"/api/v1/employees/{created['id']}",
        headers=h,
        json={"department_id": None, "employment_status_id": None},
    )
    assert r.status_code == 200, r.text


def test_headcount_reports_unassigned_bucket(api_key, admin_session):
    client = admin_session
    h = {"Authorization": f"Bearer {api_key}"}
    cid = _country_id(client, h)
    client.post(
        "/api/v1/employees/",
        headers=h,
        json={
            "employee_number": "UNA-1",
            "first_name": "Un",
            "last_name": "Assigned",
            "country_id": cid,
        },
    )
    for group in ("department", "job_title", "status"):
        hc = client.get(
            f"/api/v1/reports/headcount?group_by={group}", headers=h
        ).json()
        labels = {b["label"] for b in hc["buckets"]}
        assert "Unassigned" in labels, f"{group} missing Unassigned bucket"


def test_ui_create_minimal_employee(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Country, Employee

    db = get_session_factory()()
    cid = db.query(Country).first().id

    resp = client.post(
        "/ui/employees/new",
        data={
            "employee_number": "UIMIN-1",
            "first_name": "Ui",
            "last_name": "Minimal",
            "country_id": str(cid),
            # everything else intentionally blank
            "employment_status_id": "",
            "department_id": "",
            "job_title_id": "",
            "hire_date": "",
            "supervisor_id": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    db2 = get_session_factory()()
    emp = (
        db2.query(Employee).filter(Employee.employee_number == "UIMIN-1").first()
    )
    assert emp is not None
    assert emp.department_id is None
    assert emp.employment_status_id is None

    # The list page still renders it (null-safe department/title/status cells).
    page = client.get("/ui/employees")
    assert page.status_code == 200
    assert "UIMIN-1" in page.text
