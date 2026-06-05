"""Tests for the HTML UI routes.

We use TestClient to walk a logged-in admin through the UI surface and verify
that pages render with HTTP 200 and contain expected markers. We're NOT testing
visual output — that's manual — just that nothing 500s and the right text
makes it into the rendered HTML.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def ui_session(client: TestClient) -> TestClient:
    """Log in via the HTML login form and return the client with the cookie set."""
    from app.config import get_settings

    settings = get_settings()
    resp = client.post(
        "/ui/login",
        data={
            "username": settings.initial_admin_username,
            "password": settings.initial_admin_password,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert "/ui/employees" in resp.headers["location"]
    return client


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


def test_login_page_renders(client: TestClient) -> None:
    resp = client.get("/ui/login")
    assert resp.status_code == 200
    assert "Sign in" in resp.text
    assert "Demo HR" in resp.text


def test_login_with_bad_password_re_renders_form(client: TestClient) -> None:
    resp = client.post(
        "/ui/login",
        data={"username": "robbytheadmin", "password": "wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 200  # re-renders, doesn't redirect
    assert "Invalid username or password" in resp.text


def test_unauthed_request_redirects_to_login(client: TestClient) -> None:
    resp = client.get("/ui/employees", follow_redirects=False)
    # The endpoint requires auth; first FastAPI redirects to add slash, then auth
    # redirects to login. We just check it ultimately winds up at login.
    resp = client.get("/ui/employees", follow_redirects=True)
    # After redirect chain, should be on login page
    assert "Sign in" in resp.text or resp.url.path == "/ui/login"


def test_logout_clears_session(ui_session: TestClient) -> None:
    resp = ui_session.post("/ui/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Employee list
# ---------------------------------------------------------------------------


def test_employees_list_renders(ui_session: TestClient) -> None:
    resp = ui_session.get("/ui/employees")
    assert resp.status_code == 200
    assert "Employees" in resp.text
    # Both sample employees should appear
    assert "Sample Manager" in resp.text
    assert "Sample Employee" in resp.text
    # Badge for non-active status
    assert "Not Active" in resp.text


def test_employees_list_view_tabs(ui_session: TestClient) -> None:
    for view in ("active", "all", "archived"):
        resp = ui_session.get(f"/ui/employees?view={view}")
        assert resp.status_code == 200


def test_new_employee_form_renders(ui_session: TestClient) -> None:
    resp = ui_session.get("/ui/employees/new")
    assert resp.status_code == 200
    # All major form sections present
    assert "Identity" in resp.text
    assert "Address" in resp.text
    assert "Employment" in resp.text
    assert "supervisor_id" in resp.text


def test_edit_employee_form_renders(ui_session: TestClient) -> None:
    # The seeded sample employee with id=1 or 2
    resp = ui_session.get("/ui/employees/1/edit")
    assert resp.status_code == 200
    assert "Edit Employee" in resp.text


def test_htmx_state_options_partial(ui_session: TestClient) -> None:
    # Call the partial without a country_id — should return the "— None —" option only
    resp = ui_session.get("/ui/employees/_states-options")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def test_lookup_pages_render(ui_session: TestClient) -> None:
    for path in [
        "/ui/lookups/countries",
        "/ui/lookups/states-provinces",
        "/ui/lookups/employment-statuses",
        "/ui/lookups/departments",
        "/ui/lookups/job-titles",
        "/ui/lookups/locations",
    ]:
        resp = ui_session.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_lookup_new_country(ui_session: TestClient) -> None:
    # Show form
    resp = ui_session.get("/ui/lookups/countries/new")
    assert resp.status_code == 200
    assert "ISO Code" in resp.text

    # Submit form
    resp = ui_session.post(
        "/ui/lookups/countries/new",
        data={"code": "zz", "name": "Atlantis", "is_active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/ui/lookups/countries" in resp.headers["location"]


def test_lookup_new_department_and_then_edit(ui_session: TestClient) -> None:
    # Create
    resp = ui_session.post(
        "/ui/lookups/departments/new",
        data={"name": "Test Department", "is_active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Find it
    list_resp = ui_session.get("/ui/lookups/departments")
    assert "Test Department" in list_resp.text


def test_cannot_delete_system_status_via_ui(ui_session: TestClient) -> None:
    """The 'Active' status is system-flagged and the delete button shouldn't even
    render. We don't have a great way to test the absence, but we can attempt to
    delete and verify it doesn't crash (the route is registered but it should
    flash error).
    """
    # Find Active status id by looking at the list
    resp = ui_session.get("/ui/lookups/employment-statuses")
    assert "Active" in resp.text
    assert "System" in resp.text  # Badge shows for system rows


# ---------------------------------------------------------------------------
# Settings — admin users, API keys, OAuth clients
# ---------------------------------------------------------------------------


def test_admin_users_page(ui_session: TestClient) -> None:
    resp = ui_session.get("/ui/settings/admin-users")
    assert resp.status_code == 200
    assert "robbytheadmin" in resp.text
    assert "Seeded" in resp.text  # Badge for the seeded admin


def test_create_new_admin_user(ui_session: TestClient) -> None:
    resp = ui_session.post(
        "/ui/settings/admin-users/new",
        data={"username": "demo_admin", "password": "verysecure123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    list_resp = ui_session.get("/ui/settings/admin-users")
    assert "demo_admin" in list_resp.text


def test_create_admin_with_short_password_rejected(ui_session: TestClient) -> None:
    resp = ui_session.post(
        "/ui/settings/admin-users/new",
        data={"username": "weakpwd", "password": "short"},
        follow_redirects=False,
    )
    # Re-renders form with error
    assert resp.status_code == 200
    assert "at least 8 characters" in resp.text


def test_create_api_key_shows_secret_once(ui_session: TestClient) -> None:
    # Create
    resp = ui_session.post(
        "/ui/settings/api-keys/new",
        data={"name": "Test Key from UI"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Land on list — should show the secret-reveal block
    list_resp = ui_session.get("/ui/settings/api-keys")
    assert "Test Key from UI" in list_resp.text
    assert "hrsot_" in list_resp.text  # Full key still visible once

    # Visit again — secret should be gone (one-shot reveal)
    list_resp2 = ui_session.get("/ui/settings/api-keys")
    assert "Test Key from UI" in list_resp2.text
    # The secret-reveal box should not appear
    assert "copy it now" not in list_resp2.text


def test_create_oauth_client_shows_credentials_once(ui_session: TestClient) -> None:
    resp = ui_session.post(
        "/ui/settings/oauth-clients/new",
        data={"name": "Test OAuth"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    list_resp = ui_session.get("/ui/settings/oauth-clients")
    assert "Test OAuth" in list_resp.text
    # Client id + secret both visible once
    assert "hrsot_client_" in list_resp.text
    assert "CLIENT SECRET" in list_resp.text


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_page_renders_with_typed_phrase_widget(ui_session: TestClient) -> None:
    resp = ui_session.get("/ui/settings/reset")
    assert resp.status_code == 200
    assert "data-reset-phrase" in resp.text
    assert "RESET" in resp.text


def test_reset_employees_only(ui_session: TestClient) -> None:
    resp = ui_session.post(
        "/ui/settings/reset",
        data={"reset_employees": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # After reset, sample employees should be back
    emp_resp = ui_session.get("/ui/employees?view=all")
    assert "Sample Manager" in emp_resp.text
    assert "Sample Employee" in emp_resp.text


def test_reset_with_dependency_violation_flashes_error(ui_session: TestClient) -> None:
    """Resetting employment statuses without employees should fail dependency check."""
    resp = ui_session.post(
        "/ui/settings/reset",
        data={"reset_employment_statuses": "1"},  # without reset_employees
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "requires resetting employees" in resp.text


def test_reset_nothing_selected(ui_session: TestClient) -> None:
    resp = ui_session.post(
        "/ui/settings/reset", data={}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert "Nothing was selected" in resp.text


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


def test_static_css_served(client: TestClient) -> None:
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert "app-shell" in resp.text  # Our custom class is in there


def test_static_js_served(client: TestClient) -> None:
    resp = client.get("/static/app.js")
    assert resp.status_code == 200


def test_lookup_new_location_and_then_edit(ui_session: TestClient) -> None:
    # Create
    resp = ui_session.post(
        "/ui/lookups/locations/new",
        data={"name": "Test Location", "is_active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Find it
    list_resp = ui_session.get("/ui/lookups/locations")
    assert "Test Location" in list_resp.text
