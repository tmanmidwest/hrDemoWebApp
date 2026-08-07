"""Read-only detail page + editable custom fields on the employee form."""

from __future__ import annotations


def _make(db):
    """Create text/boolean/number custom fields and an employee. Returns (emp_id, country_id)."""
    from app.models import Country, CustomFieldDefinition, Employee

    country_id = db.query(Country).first().id
    db.add_all(
        [
            CustomFieldDefinition(key="worker_type", label="Worker Type", data_type="text", display_order=10),
            CustomFieldDefinition(key="has_account", label="Has Account", data_type="boolean", display_order=20),
            CustomFieldDefinition(key="level", label="Level", data_type="number", display_order=30),
        ]
    )
    emp = Employee(
        employee_number="DET-1",
        first_name="Dana",
        last_name="Detail",
        country_id=country_id,
        custom_fields={"worker_type": "EMP", "has_account": True, "level": 3},
    )
    db.add(emp)
    db.commit()
    return emp.id, country_id


def _edit_payload(country_id, **overrides):
    base = {
        "employee_number": "DET-1",
        "first_name": "Dana",
        "last_name": "Detail",
        "country_id": str(country_id),
        "employment_status_id": "",
        "department_id": "",
        "job_title_id": "",
        "hire_date": "",
        "supervisor_id": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


def test_detail_page_renders_readonly_with_custom_and_edit_link(admin_session):
    client = admin_session
    from app.db import get_session_factory

    emp_id, _ = _make(get_session_factory()())

    resp = client.get(f"/ui/employees/{emp_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Dana Detail" in body
    assert "Worker Type" in body and "EMP" in body  # custom attr shown
    assert f"/ui/employees/{emp_id}/edit" in body  # Edit button present


def test_new_route_not_shadowed_by_detail(admin_session):
    """GET /ui/employees/new must still render the create form, not 404/500."""
    resp = admin_session.get("/ui/employees/new")
    assert resp.status_code == 200
    assert "Create employee" in resp.text or "Employee Number" in resp.text


def test_list_links_name_to_detail(admin_session):
    client = admin_session
    from app.db import get_session_factory

    emp_id, _ = _make(get_session_factory()())
    resp = client.get("/ui/employees?view=all")
    assert resp.status_code == 200
    assert f'href="/ui/employees/{emp_id}"' in resp.text


# ---------------------------------------------------------------------------
# Editable custom fields
# ---------------------------------------------------------------------------


def test_edit_updates_custom_fields(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Employee

    emp_id, country_id = _make(get_session_factory()())

    resp = client.post(
        f"/ui/employees/{emp_id}/edit",
        data=_edit_payload(
            country_id,
            cf_worker_type="CWR",
            cf_has_account="false",
            cf_level="7",
        ),
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text

    emp = get_session_factory()().get(Employee, emp_id)
    assert emp.custom_fields["worker_type"] == "CWR"
    assert emp.custom_fields["has_account"] is False
    assert emp.custom_fields["level"] == 7


def test_edit_blank_clears_custom_field(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Employee

    emp_id, country_id = _make(get_session_factory()())

    client.post(
        f"/ui/employees/{emp_id}/edit",
        data=_edit_payload(country_id, cf_worker_type="", cf_has_account="", cf_level=""),
        follow_redirects=False,
    )
    emp = get_session_factory()().get(Employee, emp_id)
    assert "worker_type" not in emp.custom_fields
    assert "has_account" not in emp.custom_fields
    assert "level" not in emp.custom_fields


def test_edit_invalid_number_custom_field_reports_error(admin_session):
    client = admin_session
    from app.db import get_session_factory
    from app.models import Employee

    emp_id, country_id = _make(get_session_factory()())

    resp = client.post(
        f"/ui/employees/{emp_id}/edit",
        data=_edit_payload(country_id, cf_level="not-a-number"),
        follow_redirects=False,
    )
    # Re-renders the form with an error (200), does not save.
    assert resp.status_code == 200
    assert "Level" in resp.text
    emp = get_session_factory()().get(Employee, emp_id)
    assert emp.custom_fields["level"] == 3  # unchanged
