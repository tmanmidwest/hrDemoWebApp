"""Tests for the aggregate reporting endpoints (/api/v1/reports/*).

Exercised against the seeded demo data: 2 employees, both in Engineering, one a
manager (Active) and one their direct report (Not Active), neither with a
location assigned.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _make_key(admin_session: TestClient, scopes: list[str] | None) -> dict[str, str]:
    body: dict = {"name": "reports-test"}
    if scopes is not None:
        body["scopes"] = scopes
    resp = admin_session.post("/api/v1/auth/api-keys/", json=body)
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['key']}"}


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------


def test_reports_require_reports_scope(admin_session: TestClient) -> None:
    """A key without reports:read is denied; one with it is allowed."""
    no_scope = _make_key(admin_session, ["employees:read"])
    for path in ("/api/v1/reports/headcount", "/api/v1/reports/org", "/api/v1/reports/activity"):
        assert admin_session.get(path, headers=no_scope).status_code == 403, path

    ok = _make_key(admin_session, ["reports:read"])
    for path in ("/api/v1/reports/headcount", "/api/v1/reports/org", "/api/v1/reports/activity"):
        assert admin_session.get(path, headers=ok).status_code == 200, path


def test_reports_require_auth(client: TestClient) -> None:
    """No bearer token at all is a 401."""
    assert client.get("/api/v1/reports/headcount").status_code == 401


# ---------------------------------------------------------------------------
# Headcount
# ---------------------------------------------------------------------------


def test_headcount_by_department(auth_headers: dict[str, str], client: TestClient) -> None:
    resp = client.get(
        "/api/v1/reports/headcount", params={"group_by": "department"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["group_by"] == "department"
    assert body["total"] == 2
    eng = [b for b in body["buckets"] if b["label"] == "Engineering"]
    assert eng and eng[0]["count"] == 2
    # Buckets are ordered by count descending.
    counts = [b["count"] for b in body["buckets"]]
    assert counts == sorted(counts, reverse=True)


def test_headcount_by_status_sums_to_total(
    auth_headers: dict[str, str], client: TestClient
) -> None:
    resp = client.get(
        "/api/v1/reports/headcount", params={"group_by": "status"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sum(b["count"] for b in body["buckets"]) == body["total"] == 2


def test_headcount_nullable_location_unassigned_bucket(
    auth_headers: dict[str, str], client: TestClient
) -> None:
    """Seed employees have no location, so they land in an 'Unassigned' bucket."""
    resp = client.get(
        "/api/v1/reports/headcount", params={"group_by": "location"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    unassigned = [b for b in body["buckets"] if b["label"] == "Unassigned"]
    assert unassigned and unassigned[0]["key"] is None
    assert unassigned[0]["count"] == 2
    assert body["total"] == 2


def test_headcount_rejects_bad_group_by(
    auth_headers: dict[str, str], client: TestClient
) -> None:
    resp = client.get(
        "/api/v1/reports/headcount", params={"group_by": "nope"}, headers=auth_headers
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Org
# ---------------------------------------------------------------------------


def test_org_report(auth_headers: dict[str, str], client: TestClient) -> None:
    resp = client.get("/api/v1/reports/org", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_employees"] == 2
    assert body["total_managers"] == 1
    assert body["individual_contributors"] == 1
    assert body["without_supervisor"] == 1
    assert body["max_span"] == 1
    assert body["managers"][0]["direct_reports"] == 1


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


def test_activity_report_records_and_buckets(
    auth_headers: dict[str, str], client: TestClient
) -> None:
    # Running a report is itself an audited event, so after these calls the
    # activity report must show the 'report' category.
    client.get("/api/v1/reports/headcount", headers=auth_headers)
    resp = client.get("/api/v1/reports/activity", params={"days": 1}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_days"] == 1
    assert body["total_events"] > 0
    categories = {b["key"] for b in body["by_category"]}
    assert "report" in categories
