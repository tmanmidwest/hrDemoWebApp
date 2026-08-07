"""The employees list Department filter supports an is / is-not (exclude) mode."""

from __future__ import annotations


def _setup(db):
    """Create a POC dept + a normal dept and three employees:
    one in POC, one in the normal dept, one with no department. Returns poc id."""
    from app.models import Country, Department, Employee

    country_id = db.query(Country).first().id
    poc = Department(name="POC User", is_active=True)
    eng = Department(name="Engineering X", is_active=True)
    db.add_all([poc, eng])
    db.flush()
    db.add_all(
        [
            Employee(
                employee_number="POC-1",
                first_name="Poc",
                last_name="Person",
                country_id=country_id,
                department_id=poc.id,
            ),
            Employee(
                employee_number="ENG-1",
                first_name="Eng",
                last_name="Person",
                country_id=country_id,
                department_id=eng.id,
            ),
            Employee(
                employee_number="NONE-1",
                first_name="No",
                last_name="Dept",
                country_id=country_id,
                department_id=None,
            ),
        ]
    )
    db.commit()
    return poc.id


def test_department_is_not_excludes_but_keeps_others_and_unassigned(admin_session):
    client = admin_session
    from app.db import get_session_factory

    poc_id = _setup(get_session_factory()())

    resp = client.get(
        f"/ui/employees?view=all&f_department={poc_id}&f_department_mode=is_not"
    )
    assert resp.status_code == 200
    body = resp.text
    assert "POC-1" not in body  # POC User department excluded
    assert "ENG-1" in body  # other department kept
    assert "NONE-1" in body  # unassigned kept (not silently dropped)


def test_department_is_mode_still_includes(admin_session):
    client = admin_session
    from app.db import get_session_factory

    poc_id = _setup(get_session_factory()())

    resp = client.get(
        f"/ui/employees?view=all&f_department={poc_id}&f_department_mode=is"
    )
    assert resp.status_code == 200
    body = resp.text
    assert "POC-1" in body
    assert "ENG-1" not in body
    assert "NONE-1" not in body
