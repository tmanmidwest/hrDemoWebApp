"""Tests for the admin Data Import wizard and custom fields.

Uses a synthesized CSV in the same shape as a customer HR extract (name in
"Last, First" form, department/title/location by name, country/status codes,
supervisor by id, and several columns with no home in the schema) so the test is
self-contained and doesn't depend on any local file.
"""

from __future__ import annotations

import pytest

from app.services import data_import
from app.services.custom_fields import coerce_value, slugify_key

# A small extract: two of the rows supervise the others (self-contained org).
SAMPLE_CSV = (
    "ID,Name,Dept Descr,Job Title,Loc Country,Loc State,Pay Status,"
    "Supv ID,Org Relation,Full/Part\n"
    "1001,\"Melvin, Zachary\",Corp - IT,Manager,USA,AL,A,,EMP,F\n"
    "1002,\"Brooks, Isaac\",Corp - IT,Specialist,USA,AL,A,1001,EMP,F\n"
    "1003,\"Nunez, Sofia\",Cloud Svcs,Engineer,MEX,JAL,A,1001,CWR,P\n"
)


def _upload(client, csv_text: str, filename: str = "extract.csv"):
    return client.post(
        "/ui/admin/import/upload",
        files={"file": (filename, csv_text.encode(), "text/csv")},
        follow_redirects=False,
    )


def _batch_id_from_redirect(resp) -> str:
    # /ui/admin/import/<id>/map
    return resp.headers["location"].split("/")[-2]


# ---------------------------------------------------------------------------
# Unit-level: mapping + coercion helpers
# ---------------------------------------------------------------------------


def test_suggest_mapping_routes_columns():
    cols = ["ID", "Name", "Dept Descr", "Job Title", "Org Relation", "SPV Name"]
    m = data_import.suggest_mapping(cols)
    assert m["ID"]["target"] == "employee_number"
    assert m["Name"]["target"] == data_import.TARGET_SPLIT_NAME
    assert m["Dept Descr"]["target"] == "department"
    assert m["Job Title"]["target"] == "job_title"
    assert m["Org Relation"]["target"] == "custom:org_relation"
    assert m["SPV Name"]["target"] == data_import.TARGET_IGNORE


def test_split_name():
    assert data_import._split_name("Brooks, Isaac") == ("Brooks", "Isaac")
    assert data_import._split_name("Jane Doe") == ("Doe", "Jane")


def test_coerce_value_types():
    assert coerce_value("boolean", "Yes") is True
    assert coerce_value("boolean", "n") is False
    assert coerce_value("number", "42") == 42
    assert coerce_value("date", "01/15/2026") == "2026-01-15"
    assert coerce_value("text", "  ") is None


def test_slugify_key():
    assert slugify_key("Org Relation") == "org_relation"
    assert slugify_key("Full/Part") == "full_part"


# ---------------------------------------------------------------------------
# End-to-end wizard flow
# ---------------------------------------------------------------------------


def test_wizard_full_flow_creates_employees_lookups_custom_fields(admin_session):
    client = admin_session

    assert client.get("/ui/admin/import").status_code == 200

    r = _upload(client, SAMPLE_CSV)
    assert r.status_code == 303
    bid = _batch_id_from_redirect(r)

    assert client.get(f"/ui/admin/import/{bid}/map").status_code == 200
    assert client.get(f"/ui/admin/import/{bid}/resolve").status_code == 200

    preview = client.get(f"/ui/admin/import/{bid}/preview")
    assert preview.status_code == 200
    assert "3" in preview.text  # 3 new rows

    r = client.post(f"/ui/admin/import/{bid}/commit", follow_redirects=False)
    assert r.status_code == 303
    done = client.get(f"/ui/admin/import/{bid}/done")
    assert done.status_code == 200
    assert "Import complete" in done.text

    # Employees landed with the expected data.
    from app.db import get_session_factory
    from app.models import CustomFieldDefinition, Department, Employee
    from app.schemas.employee import EmployeeOut

    db = get_session_factory()()
    isaac = (
        db.query(Employee).filter(Employee.employee_number == "1002").first()
    )
    assert isaac is not None
    assert isaac.custom_fields["org_relation"] == "EMP"
    assert isaac.custom_fields["full_part"] == "F"
    # Supervisor resolved by id from within the same file.
    assert isaac.supervisor is not None
    assert isaac.supervisor.employee_number == "1001"
    # hire_date was absent in the source and is now allowed to be null.
    assert isaac.hire_date is None

    # custom_fields is surfaced by the API schema (what the connector reads).
    dto = EmployeeOut.model_validate(isaac)
    assert dto.custom_fields["org_relation"] == "EMP"
    assert dto.hire_date is None

    # Custom field definitions were created (org_relation, full_part).
    keys = {d.key for d in db.query(CustomFieldDefinition)}
    assert {"org_relation", "full_part"} <= keys
    # Missing department was auto-created.
    assert db.query(Department).filter(Department.name == "Cloud Svcs").first()


def test_wizard_reimport_is_idempotent(admin_session):
    client = admin_session

    # First import.
    r = _upload(client, SAMPLE_CSV)
    bid = _batch_id_from_redirect(r)
    client.post(f"/ui/admin/import/{bid}/commit", follow_redirects=False)

    from app.db import get_session_factory
    from app.models import Department, Employee, JobTitle, Location

    db = get_session_factory()()
    counts_before = (
        db.query(Employee).count(),
        db.query(Department).count(),
        db.query(JobTitle).count(),
        db.query(Location).count(),
    )

    # Second import of the same data → updates only, no duplicate lookups.
    r = _upload(client, SAMPLE_CSV)
    bid2 = _batch_id_from_redirect(r)
    client.post(f"/ui/admin/import/{bid2}/commit", follow_redirects=False)

    db2 = get_session_factory()()
    counts_after = (
        db2.query(Employee).count(),
        db2.query(Department).count(),
        db2.query(JobTitle).count(),
        db2.query(Location).count(),
    )
    assert counts_before == counts_after


def test_save_and_apply_profile(admin_session):
    client = admin_session

    # Upload, then save a profile on the map step.
    r = _upload(client, SAMPLE_CSV)
    bid = _batch_id_from_redirect(r)

    # Re-post the mapping the wizard already computed, with save_profile set.
    # Pull current mapping from the batch to echo it back.
    from app.db import get_session_factory
    from app.models import ImportBatch, ImportProfile

    db = get_session_factory()()
    batch = db.get(ImportBatch, int(bid))
    sources = list(batch.source_columns)
    targets = []
    for col in sources:
        spec = batch.column_map[col]
        t = spec["target"]
        if t.startswith("custom:"):
            t = f"custom:{spec.get('data_type', 'text')}"
        targets.append(t)

    # httpx encodes list values as repeated form keys.
    form = {
        "source": sources,
        "target": targets,
        "save_profile": "1",
        "profile_name": "Acme POC",
    }
    resp = client.post(
        f"/ui/admin/import/{bid}/map", data=form, follow_redirects=False
    )
    assert resp.status_code == 303

    db2 = get_session_factory()()
    prof = db2.query(ImportProfile).filter(ImportProfile.name == "Acme POC").first()
    assert prof is not None
    assert prof.column_map  # captured the mapping

    # The profile shows up on the landing page.
    landing = client.get("/ui/admin/import")
    assert "Acme POC" in landing.text


def test_import_requires_admin(client):
    # Not logged in → redirected to login (303), not a 200 wizard page.
    resp = client.get("/ui/admin/import", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/ui/login" in resp.headers.get("location", "")
