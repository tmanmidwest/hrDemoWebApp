"""Tests for the live connector-schema export (service, API endpoint, UI)."""

from __future__ import annotations

import json


def _make_custom_employee(db):
    """Create one custom-field definition and an employee carrying a value."""
    from app.models import (
        Country,
        CustomFieldDefinition,
        Department,
        Employee,
        EmploymentStatus,
        JobTitle,
    )

    country = db.query(Country).first()
    status = db.query(EmploymentStatus).first()
    dept = Department(name="Schema Dept", is_active=True)
    db.add(dept)
    db.flush()
    title = JobTitle(department_id=dept.id, name="Schema Title", is_active=True)
    db.add(title)
    db.add(
        CustomFieldDefinition(
            key="worker_type", label="Worker Type", data_type="text", display_order=10
        )
    )
    db.flush()
    db.add(
        Employee(
            employee_number="SCH-1",
            first_name="Sam",
            last_name="Schema",
            country_id=country.id,
            employment_status_id=status.id,
            department_id=dept.id,
            job_title_id=title.id,
            work_email="sam@example.com",
            custom_fields={"worker_type": "EMP"},
        )
    )
    db.commit()


def test_build_schema_includes_core_reference_and_custom(_isolated_data_dir):
    from app.config import get_settings
    from app.db import get_session_factory
    from app.services.migrations import run_migrations
    from app.services.schema_export import build_schema, schema_to_csv
    from app.services.seed_data import seed_database

    run_migrations()
    db = get_session_factory()()
    seed_database(db, get_settings())
    _make_custom_employee(db)

    schema = build_schema(db)
    paths = {a["path"] for a in schema["attributes"]}
    # Core, reference, and the instance-specific custom field are all present.
    assert "employee_number" in paths
    assert "department.name" in paths
    assert "employment_status.value" in paths
    assert "supervisor.employee_number" in paths
    assert "custom_fields.worker_type" in paths

    # The custom attribute is tagged and typed.
    wt = next(a for a in schema["attributes"] if a["path"] == "custom_fields.worker_type")
    assert wt["group"] == "custom"
    assert wt["type"] == "string"

    # Enumerations + a masked sample record are included.
    assert schema["enumerations"]["employment_status"]
    assert schema["sample_record"] is not None
    assert "ssn" not in schema["sample_record"]  # raw SSN never present
    assert "ssn_masked" in schema["sample_record"]

    # CSV renders with a header and one row per attribute.
    csv_text = schema_to_csv(schema)
    assert csv_text.splitlines()[0] == "path,type,nullable,group,example,description"
    assert "custom_fields.worker_type" in csv_text


def test_schema_api_endpoint(api_key, admin_session):
    client = admin_session
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = client.get("/api/v1/employees/schema", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "attributes" in body
    assert any(a["path"] == "employee_number" for a in body["attributes"])
    # The literal /schema path wins over /{employee_id} (which would 422 on "schema").


def test_schema_endpoint_requires_auth(client):
    resp = client.get("/api/v1/employees/schema")
    assert resp.status_code in (401, 403)


def test_ui_schema_page_and_downloads(admin_session):
    client = admin_session
    assert client.get("/ui/admin/schema").status_code == 200

    j = client.get("/ui/admin/schema/export.json")
    assert j.status_code == 200
    assert j.headers["content-type"].startswith("application/json")
    parsed = json.loads(j.content)
    assert "attributes" in parsed

    c = client.get("/ui/admin/schema/export.csv")
    assert c.status_code == 200
    assert "text/csv" in c.headers["content-type"]
    assert c.text.splitlines()[0].startswith("path,type,nullable")
