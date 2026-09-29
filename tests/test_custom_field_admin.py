"""Admin management of custom field definitions, and required-ness enforcement.

Covers the CRUD screens at /ui/admin/custom-fields plus the three write surfaces
a required custom field has to hold on: the employee form, the REST API, and CSV
import.
"""

from __future__ import annotations


def _country_id(db):
    from app.models import Country

    return db.query(Country).first().id


def _session():
    from app.db import get_session_factory

    return get_session_factory()()


def _make_field(db, **overrides):
    from app.models import CustomFieldDefinition

    kwargs = {
        "key": "worker_type",
        "label": "Worker Type",
        "data_type": "text",
        "display_order": 10,
    }
    kwargs.update(overrides)
    d = CustomFieldDefinition(**kwargs)
    db.add(d)
    db.commit()
    return d.id


def _emp_payload(country_id, **overrides):
    base = {
        "employee_number": "CF-1",
        "first_name": "Casey",
        "last_name": "Field",
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
# CRUD screens
# ---------------------------------------------------------------------------


def test_list_page_empty_then_populated(admin_session):
    resp = admin_session.get("/ui/admin/custom-fields")
    assert resp.status_code == 200
    assert "No custom fields yet" in resp.text

    _make_field(_session(), label="Cost Center Code", key="cost_center_code")
    resp = admin_session.get("/ui/admin/custom-fields")
    assert "Cost Center Code" in resp.text
    assert "cost_center_code" in resp.text


def test_create_field_derives_key_and_persists_type_and_required(admin_session):
    resp = admin_session.post(
        "/ui/admin/custom-fields/new",
        data={
            "label": "Badge Expiry",
            "key": "",  # derived from the label
            "data_type": "date",
            "description": "When the site badge lapses.",
            "display_order": "20",
            "include_in_export": "1",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.models import CustomFieldDefinition

    d = _session().query(CustomFieldDefinition).one()
    assert d.key == "badge_expiry"
    assert d.data_type == "date"
    assert d.is_required is False
    assert d.include_in_export is True
    assert d.description == "When the site badge lapses."


def test_create_rejects_reserved_and_invalid_keys(admin_session):
    resp = admin_session.post(
        "/ui/admin/custom-fields/new",
        data={"label": "Department", "data_type": "text", "is_active": "1"},
    )
    assert resp.status_code == 200
    assert "reserved" in resp.text.lower()

    from app.models import CustomFieldDefinition

    assert _session().query(CustomFieldDefinition).count() == 0


def test_duplicate_key_is_reported_not_crashed(admin_session):
    _make_field(_session())
    resp = admin_session.post(
        "/ui/admin/custom-fields/new",
        data={
            "label": "Worker Type",
            "key": "worker_type",
            "data_type": "text",
            "is_active": "1",
        },
    )
    assert resp.status_code == 200
    assert "already exists" in resp.text


def test_edit_updates_label_and_order_but_never_the_key(admin_session):
    field_id = _make_field(_session())
    resp = admin_session.post(
        f"/ui/admin/custom-fields/{field_id}/edit",
        data={
            "label": "Worker Class",
            "key": "something_else",  # must be ignored
            "data_type": "text",
            "display_order": "5",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.models import CustomFieldDefinition

    d = _session().get(CustomFieldDefinition, field_id)
    assert d.label == "Worker Class"
    assert d.key == "worker_type"
    assert d.display_order == 5
    assert d.include_in_export is False  # checkbox omitted → cleared


def test_cannot_retype_a_field_that_holds_values(admin_session):
    db = _session()
    field_id = _make_field(db)
    from app.models import Employee

    db.add(
        Employee(
            employee_number="CF-9",
            first_name="Val",
            last_name="Ue",
            country_id=_country_id(db),
            custom_fields={"worker_type": "EMP"},
        )
    )
    db.commit()

    resp = admin_session.post(
        f"/ui/admin/custom-fields/{field_id}/edit",
        data={"label": "Worker Type", "data_type": "number", "is_active": "1"},
    )
    assert resp.status_code == 200
    assert "change the type" in resp.text

    from app.models import CustomFieldDefinition

    assert _session().get(CustomFieldDefinition, field_id).data_type == "text"


def test_marking_required_warns_before_it_starts_blocking(admin_session):
    db = _session()
    field_id = _make_field(db)
    from app.models import Employee

    db.add(
        Employee(
            employee_number="CF-8",
            first_name="Gap",
            last_name="Row",
            country_id=_country_id(db),
        )
    )
    db.commit()

    # First POST: no confirm → interstitial, nothing saved.
    resp = admin_session.post(
        f"/ui/admin/custom-fields/{field_id}/edit",
        data={
            "label": "Worker Type",
            "data_type": "text",
            "is_required": "1",
            "is_active": "1",
        },
    )
    assert resp.status_code == 200
    assert "have no value" in resp.text

    from app.models import CustomFieldDefinition

    assert _session().get(CustomFieldDefinition, field_id).is_required is False

    # Second POST with confirm → saved.
    resp = admin_session.post(
        f"/ui/admin/custom-fields/{field_id}/edit",
        data={
            "label": "Worker Type",
            "data_type": "text",
            "is_required": "1",
            "is_active": "1",
            "confirm": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _session().get(CustomFieldDefinition, field_id).is_required is True


def test_delete_confirms_when_values_exist_then_deletes(admin_session):
    db = _session()
    field_id = _make_field(db)
    from app.models import Employee

    db.add(
        Employee(
            employee_number="CF-7",
            first_name="Del",
            last_name="Ete",
            country_id=_country_id(db),
            custom_fields={"worker_type": "EMP"},
        )
    )
    db.commit()

    resp = admin_session.post(f"/ui/admin/custom-fields/{field_id}/delete")
    assert resp.status_code == 200
    assert "Deactivate instead" in resp.text

    from app.models import CustomFieldDefinition

    assert _session().get(CustomFieldDefinition, field_id) is not None

    resp = admin_session.post(
        f"/ui/admin/custom-fields/{field_id}/delete",
        data={"confirm": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _session().get(CustomFieldDefinition, field_id) is None


def test_non_admin_cannot_reach_the_screen(client):
    resp = client.get("/ui/admin/custom-fields", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)  # bounced to login


# ---------------------------------------------------------------------------
# Enforcement: employee form
# ---------------------------------------------------------------------------


def test_form_marks_required_fields_and_blocks_a_blank_save(admin_session):
    db = _session()
    _make_field(db, is_required=True)
    country_id = _country_id(db)

    resp = admin_session.get("/ui/employees/new")
    assert 'name="cf_worker_type"' in resp.text
    assert "label__required" in resp.text

    resp = admin_session.post(
        "/ui/employees/new",
        data=_emp_payload(country_id, cf_worker_type=""),
    )
    assert resp.status_code == 200
    assert "Worker Type is required" in resp.text

    from app.models import Employee

    assert (
        _session().query(Employee).filter(Employee.employee_number == "CF-1").first()
        is None
    )

    resp = admin_session.post(
        "/ui/employees/new",
        data=_emp_payload(country_id, cf_worker_type="EMP"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    emp = (
        _session().query(Employee).filter(Employee.employee_number == "CF-1").one()
    )
    assert emp.custom_fields["worker_type"] == "EMP"


# ---------------------------------------------------------------------------
# Enforcement: REST API
# ---------------------------------------------------------------------------


def test_api_create_requires_the_field(api_client):
    db = _session()
    _make_field(db, is_required=True)
    country_id = _country_id(db)

    body = {
        "employee_number": "API-1",
        "first_name": "Ada",
        "last_name": "Api",
        "country_id": country_id,
    }
    resp = api_client.post("/api/v1/employees/", json=body)
    assert resp.status_code == 400
    assert "Worker Type" in resp.json()["detail"]

    resp = api_client.post(
        "/api/v1/employees/", json={**body, "custom_fields": {"worker_type": "CWR"}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["custom_fields"]["worker_type"] == "CWR"


def test_api_update_validates_the_resulting_record(api_client):
    """A record predating the field can't be patched until the value is supplied."""
    db = _session()
    from app.models import Employee

    emp = Employee(
        employee_number="API-2",
        first_name="Old",
        last_name="Record",
        country_id=_country_id(db),
    )
    db.add(emp)
    db.commit()
    emp_id = emp.id
    _make_field(db, is_required=True)

    resp = api_client.patch(f"/api/v1/employees/{emp_id}", json={"city": "Chicago"})
    assert resp.status_code == 400
    assert "Worker Type" in resp.json()["detail"]

    resp = api_client.patch(
        f"/api/v1/employees/{emp_id}",
        json={"city": "Chicago", "custom_fields": {"worker_type": "EMP"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["city"] == "Chicago"


def test_api_update_is_unaffected_when_the_value_is_already_there(api_client):
    db = _session()
    _make_field(db, is_required=True)
    from app.models import Employee

    emp = Employee(
        employee_number="API-3",
        first_name="Has",
        last_name="Value",
        country_id=_country_id(db),
        custom_fields={"worker_type": "EMP"},
    )
    db.add(emp)
    db.commit()

    resp = api_client.patch(f"/api/v1/employees/{emp.id}", json={"city": "Austin"})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Enforcement + round-trip: CSV
# ---------------------------------------------------------------------------


def test_csv_export_and_template_carry_custom_columns(admin_session):
    db = _session()
    _make_field(db, is_required=True)
    from app.models import Employee

    db.add(
        Employee(
            employee_number="CSV-1",
            first_name="Rose",
            last_name="Row",
            country_id=_country_id(db),
            custom_fields={"worker_type": "EMP"},
        )
    )
    db.commit()

    resp = admin_session.get("/ui/employees/export.csv")
    assert resp.status_code == 200
    header, *body = resp.text.strip().splitlines()
    assert header.endswith("worker_type")
    assert body[0].endswith("EMP")

    resp = admin_session.get("/ui/employees/import/template.csv")
    assert resp.text.splitlines()[0].endswith("worker_type")


def _csv(columns, rows):
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def test_csv_import_reads_the_column_and_enforces_required(admin_session):
    db = _session()
    _make_field(db, is_required=True)
    from app.services import employee_import

    columns = employee_import.export_columns(db)
    row = {
        "employee_number": "IMP-1",
        "first_name": "Ima",
        "last_name": "Porter",
        "country": "United States",
        # The CSV path has its own required columns for a new row.
        "employment_status": "Active",
        "department": "Engineering",
        "job_title": "Software Engineer",
    }

    # No value for the required custom field → error, not a silent import.
    preview = employee_import.parse_and_classify(db, _csv(columns, [row]))
    assert preview.parse_error is None
    assert preview.error_count == 1
    assert any("Worker Type is required" in e for e in preview.rows[0].errors)

    # Same row with the custom column filled → clean, and coerced.
    preview = employee_import.parse_and_classify(
        db, _csv(columns, [{**row, "worker_type": "EMP"}])
    )
    assert preview.error_count == 0, preview.rows[0].errors
    assert preview.rows[0].custom_fields == {"worker_type": "EMP"}


def test_csv_import_flags_a_missing_required_column_up_front(admin_session):
    db = _session()
    _make_field(db, is_required=True)
    from app.services import employee_import

    header = ",".join(employee_import.COLUMNS)  # no custom column at all
    preview = employee_import.parse_and_classify(db, f"{header}\n")
    assert preview.parse_error is not None
    assert "worker_type" in preview.parse_error


# ---------------------------------------------------------------------------
# Connector schema
# ---------------------------------------------------------------------------


def test_schema_reports_required_custom_fields_as_not_nullable(api_client):
    db = _session()
    _make_field(db, is_required=True)
    _make_field(db, key="nickname", label="Nickname", display_order=20)

    resp = api_client.get("/api/v1/employees/schema")
    assert resp.status_code == 200
    payload = resp.json()
    by_path = {a["path"]: a for a in payload["attributes"]}
    assert by_path["custom_fields.worker_type"]["nullable"] is False
    assert by_path["custom_fields.nickname"]["nullable"] is True
    by_key = {c["key"]: c for c in payload["custom_fields"]}
    assert by_key["worker_type"]["required"] is True
    assert by_key["nickname"]["required"] is False
