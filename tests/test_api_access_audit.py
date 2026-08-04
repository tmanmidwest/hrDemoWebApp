"""Tests for the API access-audit middleware.

Every authenticated ``/api/v1`` call — reads included — should produce an
``api.request`` event, and a rejected bearer token should produce an
``api.auth_failed`` event. This is what makes an external integration's activity
visible in the Activity log.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _events(client: TestClient, query: str = "") -> list[dict]:
    """Read events back through the Activity JSON export (session-authed)."""
    resp = client.get(f"/ui/activity/export.json{query}")
    assert resp.status_code == 200
    return json.loads(resp.text)


def test_authenticated_get_is_audited(admin_session, api_key: str) -> None:
    """A GET authenticated with an API key records an api.request event."""
    headers = {"Authorization": f"Bearer {api_key}"}
    # Trailing slash avoids the 307 redirect hop, so we assert on the real call.
    resp = admin_session.get("/api/v1/employees/", headers=headers)
    assert resp.status_code == 200

    api_events = _events(admin_session, "?category=api")
    requests = [e for e in api_events if e["event_type"] == "api.request"]
    assert requests, "expected an api.request event for the GET"

    ev = next(e for e in requests if e["detail"]["path"] == "/api/v1/employees/")
    assert ev["detail"]["method"] == "GET"
    assert ev["detail"]["status_code"] == 200
    assert ev["outcome"] == "success"
    assert ev["actor_type"] == "api_key"
    assert "duration_ms" in ev["detail"]


def test_rejected_bearer_is_audited(client: TestClient, admin_session) -> None:
    """A bad bearer token records an api.auth_failed event."""
    resp = client.get(
        "/api/v1/employees", headers={"Authorization": "Bearer hrsot_totally-bogus"}
    )
    assert resp.status_code == 401

    api_events = _events(admin_session, "?category=api")
    failures = [e for e in api_events if e["event_type"] == "api.auth_failed"]
    assert failures, "expected an api.auth_failed event for the bad token"
    assert failures[0]["outcome"] == "failure"
    assert failures[0]["detail"]["status_code"] == 401

    # A broken integration should also surface in the integrations quick-view,
    # even though its actor is anonymous (the view unions category 'api').
    view_events = _events(admin_session, "?view=integrations")
    assert any(e["event_type"] == "api.auth_failed" for e in view_events)


def test_session_ui_calls_are_not_api_audited(admin_session) -> None:
    """Session-authenticated calls (no bearer) don't create api.* events."""
    # admin_session already logged in via /api/v1/auth/session/login (no bearer).
    api_events = _events(admin_session, "?category=api")
    assert api_events == []


def test_integrations_view_groups_by_actor(admin_session, api_key: str) -> None:
    """The 'integrations' quick-view surfaces api_key/oauth_client activity."""
    headers = {"Authorization": f"Bearer {api_key}"}
    admin_session.get("/api/v1/employees", headers=headers)

    view_events = _events(admin_session, "?view=integrations")
    assert view_events, "integrations view should include the API call"
    assert all(e["actor_type"] in ("api_key", "oauth_client") for e in view_events)
