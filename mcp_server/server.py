"""HR SoT MCP server — a streamable-HTTP gateway over the HR REST API.

Design
------
This server is a small, **stateless** proxy that exposes MCP tools over the HR
REST API — reads (list/get, reports) plus writes (create/update employees,
archive/restore, terminate/reactivate, and lookup create/update/delete). It uses
two credentials, both created and rotated in the app UI
(Settings → MCP) and read live from the shared data volume — this container holds
no database and needs no secrets baked in at deploy time:

* **Outbound** (server → app): its own API key, written by the app to
  ``<data_dir>/mcp_api_key``. Every tool call authenticates to the REST API with
  it. See :func:`_resolve_service_token`.
* **Inbound** (client → server): callers must present a **gateway token**; the
  :class:`~mcp_server.gateway_auth.GatewayAuthMiddleware` validates it against the
  app-synced ``<data_dir>/mcp_gateway_tokens.json``.

Both are read fresh on each request, so rotating either in the UI takes effect on
the very next call with no restart.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_server.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("hrsot-mcp")

# Stateless + JSON responses: this is a request/response gateway, not a
# long-lived session, so we don't need per-session state or SSE streaming.
mcp = FastMCP(
    settings.server_name,
    instructions=(
        "Read and manage the Demo HR Source-of-Truth system: list/get employees "
        "and lookups; run headcount, org-structure, and activity reports; and "
        "create, update, archive/restore, and terminate/reactivate employees, "
        "plus create/update/delete lookup records (departments, job titles, "
        "locations, countries, states, employment statuses). Resolve the "
        "*_id fields with list_lookups / list_employees before writing. Writes "
        "are audited and require the MCP key to hold employees:write / "
        "lookups:write."
    ),
    host=settings.bind_host,
    port=settings.bind_port,
    streamable_http_path=settings.path,
    stateless_http=True,
    json_response=True,
)

# One shared HTTP client for the process (single event loop), created lazily.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.hr_api_base_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
        )
    return _client


class ToolError(Exception):
    """Raised to surface a clean, actionable error message to the MCP client."""


def _resolve_service_token() -> str | None:
    """Resolve the outbound API token (server → app), freshly each call.

    Order: HRMCP_API_KEY (static override) → HRMCP_API_KEY_FILE → the UI-managed
    ``<data_dir>/mcp_api_key`` file. Reading live means rotating the token in the
    app UI takes effect on the very next call, no restart.
    """
    if settings.api_key:
        return settings.api_key
    candidates = []
    if settings.api_key_file:
        candidates.append(settings.api_key_file)
    candidates.append(str(settings.data_dir / "mcp_api_key"))
    from pathlib import Path

    for raw in candidates:
        path = Path(raw)
        if path.exists():
            value = path.read_text().strip()
            if value:
                return value
    return None


def _auth_header() -> dict[str, str]:
    """Bearer header with the freshly-resolved service token, or a clean error."""
    token = _resolve_service_token()
    if not token:
        raise ToolError(
            "The MCP server has no API token to reach the app. An admin needs to "
            "generate one in the app UI (Settings → MCP → Generate API token), or "
            "set HRMCP_API_KEY / HRMCP_API_KEY_FILE for a remote host."
        )
    return {"Authorization": f"Bearer {token}"}


def _handle(resp: httpx.Response) -> Any:
    """Map an HR API response to parsed JSON or a helpful ToolError.

    Surfaces the app's own error body on 4xx (validation/conflict) so the model
    can correct its input instead of guessing. A 204 (or empty body) — e.g. from
    a lookup delete — returns a small success marker rather than failing to parse.
    """
    if resp.status_code == 401:
        raise ToolError(
            "The HR API rejected the MCP server's token (401). Rotate it in the "
            "app UI (Settings → MCP)."
        )
    if resp.status_code == 403:
        raise ToolError(
            "The MCP server's token lacks the scope required for this operation "
            "(403). Write tools need the MCP key to hold employees:write / "
            "lookups:write — rotate the key in the app UI (Settings → MCP) so it "
            "picks up the current scopes."
        )
    if resp.status_code == 404:
        raise ToolError("Not found (404).")
    if resp.status_code >= 400:
        # Includes 400/409/422 — pass the app's message through for correction.
        raise ToolError(f"HR API error {resp.status_code}: {resp.text[:500]}")
    if resp.status_code == 204 or not resp.content:
        return {"ok": True, "status_code": resp.status_code}
    return resp.json()


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET the HR API with the server's service token; return parsed JSON."""
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = await _get_client().get(path, params=clean, headers=_auth_header())
    except httpx.RequestError as exc:
        raise ToolError(
            f"Could not reach the HR API at {settings.hr_api_base_url}: {exc}"
        ) from exc
    return _handle(resp)


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Send a write request (POST/PATCH/DELETE) to the HR API and return JSON.

    ``None`` values are dropped from ``json_body`` so optional/unset fields fall
    back to the app's own defaults instead of overwriting with null. Pass an empty
    dict to send a body of ``{}`` (e.g. terminate/reactivate with server defaults).
    """
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    clean_body = (
        {k: v for k, v in json_body.items() if v is not None}
        if json_body is not None
        else None
    )
    try:
        resp = await _get_client().request(
            method,
            path,
            params=clean_params or None,
            json=clean_body,
            headers=_auth_header(),
        )
    except httpx.RequestError as exc:
        raise ToolError(
            f"Could not reach the HR API at {settings.hr_api_base_url}: {exc}"
        ) from exc
    return _handle(resp)


# ---------------------------------------------------------------------------
# Employee tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_employees(
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
    department_id: int | None = None,
    employment_status_id: int | None = None,
    updated_since: str | None = None,
    sort: str = "last_name",
    order: str = "asc",
    include_reference_managers: bool = False,
) -> Any:
    """List employees, with optional filtering, sorting, and pagination.

    `updated_since` is an ISO-8601 datetime for incremental views. Archived
    (soft-deleted) employees are excluded unless `include_archived` is true.

    Static reference managers (stand-in supervisor records such as
    `margaretmanager`) are excluded unless `include_reference_managers` is true.
    They still appear as the `supervisor` on employees who report to them and
    are fetchable by id via `get_employee`.
    """
    return await _get(
        "/api/v1/employees/",
        {
            "limit": limit,
            "offset": offset,
            "include_archived": include_archived,
            "department_id": department_id,
            "employment_status_id": employment_status_id,
            "updated_since": updated_since,
            "sort": sort,
            "order": order,
            "include_reference_managers": include_reference_managers,
        },
    )


@mcp.tool()
async def get_employee(employee_id: int) -> Any:
    """Get a single employee (with nested department, title, status, location,
    supervisor) by numeric id.
    """
    return await _get(f"/api/v1/employees/{employee_id}")


# ---------------------------------------------------------------------------
# Employee write tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_employee(
    employee_number: str,
    first_name: str,
    last_name: str,
    country_id: int,
    employment_status_id: int,
    department_id: int,
    job_title_id: int,
    hire_date: str,
    supervisor_id: int | None = None,
    middle_name: str | None = None,
    date_of_birth: str | None = None,
    ssn: str | None = None,
    address_line_1: str | None = None,
    address_line_2: str | None = None,
    city: str | None = None,
    state_province_id: int | None = None,
    postal_code: str | None = None,
    home_phone: str | None = None,
    personal_email: str | None = None,
    work_email: str | None = None,
    cost_center: str | None = None,
    termination_date: str | None = None,
    location_id: int | None = None,
) -> Any:
    """Create a new employee and return the created record.

    Dates are ISO-8601 (`YYYY-MM-DD`). The `*_id` fields are foreign keys —
    resolve them first with `list_lookups` (country/status/department/job_title/
    location) and `list_employees` (supervisor). `job_title_id` must belong to
    `department_id`, and `state_province_id` (if given) must belong to
    `country_id`.

    `supervisor_id` is required except when creating the very first employee on
    an empty system. `ssn` may include separators (they're stripped to 9 digits)
    and must be unique.
    """
    return await _request(
        "POST",
        "/api/v1/employees/",
        json_body={
            "employee_number": employee_number,
            "first_name": first_name,
            "last_name": last_name,
            "country_id": country_id,
            "employment_status_id": employment_status_id,
            "department_id": department_id,
            "job_title_id": job_title_id,
            "hire_date": hire_date,
            "supervisor_id": supervisor_id,
            "middle_name": middle_name,
            "date_of_birth": date_of_birth,
            "ssn": ssn,
            "address_line_1": address_line_1,
            "address_line_2": address_line_2,
            "city": city,
            "state_province_id": state_province_id,
            "postal_code": postal_code,
            "home_phone": home_phone,
            "personal_email": personal_email,
            "work_email": work_email,
            "cost_center": cost_center,
            "termination_date": termination_date,
            "location_id": location_id,
        },
    )


@mcp.tool()
async def update_employee(
    employee_id: int,
    employee_number: str | None = None,
    first_name: str | None = None,
    middle_name: str | None = None,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    ssn: str | None = None,
    address_line_1: str | None = None,
    address_line_2: str | None = None,
    city: str | None = None,
    country_id: int | None = None,
    state_province_id: int | None = None,
    postal_code: str | None = None,
    home_phone: str | None = None,
    personal_email: str | None = None,
    work_email: str | None = None,
    cost_center: str | None = None,
    employment_status_id: int | None = None,
    employment_status_value: int | None = None,
    department_id: int | None = None,
    job_title_id: int | None = None,
    hire_date: str | None = None,
    termination_date: str | None = None,
    supervisor_id: int | None = None,
    location_id: int | None = None,
) -> Any:
    """Partially update an employee (PATCH). Only the fields you pass are changed;
    omitted fields are left as-is. Returns the updated record.

    Status can be set by `employment_status_id` (DB primary key) OR
    `employment_status_value` (stable IGA code: 1=Active, 0=Not Active,
    3=Terminated) — supplying both is an error. To disable/terminate an employee
    prefer the dedicated `archive_employee` / `terminate_employee` tools.
    """
    return await _request(
        "PATCH",
        f"/api/v1/employees/{employee_id}",
        json_body={
            "employee_number": employee_number,
            "first_name": first_name,
            "middle_name": middle_name,
            "last_name": last_name,
            "date_of_birth": date_of_birth,
            "ssn": ssn,
            "address_line_1": address_line_1,
            "address_line_2": address_line_2,
            "city": city,
            "country_id": country_id,
            "state_province_id": state_province_id,
            "postal_code": postal_code,
            "home_phone": home_phone,
            "personal_email": personal_email,
            "work_email": work_email,
            "cost_center": cost_center,
            "employment_status_id": employment_status_id,
            "employment_status_value": employment_status_value,
            "department_id": department_id,
            "job_title_id": job_title_id,
            "hire_date": hire_date,
            "termination_date": termination_date,
            "supervisor_id": supervisor_id,
            "location_id": location_id,
        },
    )


@mcp.tool()
async def archive_employee(employee_id: int) -> Any:
    """Soft-delete (disable) an employee: the record is kept but hidden from
    default list views and supervisor pickers. Reversible with `restore_employee`.
    Idempotent.
    """
    return await _request("POST", f"/api/v1/employees/{employee_id}/archive")


@mcp.tool()
async def restore_employee(employee_id: int) -> Any:
    """Reverse `archive_employee`: un-hide a previously archived employee."""
    return await _request("POST", f"/api/v1/employees/{employee_id}/restore")


@mcp.tool()
async def terminate_employee(
    employee_id: int, termination_date: str | None = None
) -> Any:
    """Terminate an employee: atomically set status to Terminated and stamp the
    termination date. `termination_date` is ISO-8601 (`YYYY-MM-DD`) and defaults
    to today (UTC); it must be on or after the employee's hire_date. Idempotent;
    refuses on archived employees (restore them first).
    """
    return await _request(
        "POST",
        f"/api/v1/employees/{employee_id}/terminate",
        json_body={"termination_date": termination_date},
    )


@mcp.tool()
async def reactivate_employee(
    employee_id: int, employment_status_value: int | None = None
) -> Any:
    """Reverse a termination: set status back to active and clear the termination
    date. `employment_status_value` defaults to 1 (Active). Idempotent; refuses on
    archived employees.
    """
    return await _request(
        "POST",
        f"/api/v1/employees/{employee_id}/reactivate",
        json_body={"employment_status_value": employment_status_value},
    )


# ---------------------------------------------------------------------------
# Lookup tools
# ---------------------------------------------------------------------------

_LOOKUP_PATHS = {
    "countries": "/api/v1/countries/",
    "states": "/api/v1/states-provinces/",
    "statuses": "/api/v1/employment-statuses/",
    "departments": "/api/v1/departments/",
    "job_titles": "/api/v1/job-titles/",
    "locations": "/api/v1/locations/",
}


@mcp.tool()
async def list_lookups(kind: str) -> Any:
    """List reference/lookup records used across employee data.

    `kind` is one of: countries, states, statuses, departments, job_titles,
    locations.
    """
    path = _LOOKUP_PATHS.get(kind)
    if path is None:
        raise ToolError(
            f"Unknown lookup kind '{kind}'. Choose one of: "
            + ", ".join(sorted(_LOOKUP_PATHS))
        )
    return await _get(path)


def _lookup_path(kind: str) -> str:
    path = _LOOKUP_PATHS.get(kind)
    if path is None:
        raise ToolError(
            f"Unknown lookup kind '{kind}'. Choose one of: "
            + ", ".join(sorted(_LOOKUP_PATHS))
        )
    return path


@mcp.tool()
async def create_lookup(kind: str, fields: dict[str, Any]) -> Any:
    """Create a lookup/reference record and return it.

    `kind` is one of: countries, states, statuses, departments, job_titles,
    locations. `fields` holds the record's values ([bracketed] ones are optional):

    - countries: code (2-letter ISO), name, [is_active]
    - states: country_id, name, [code], [is_active]
    - statuses: label, value (int), [is_active_status]
    - departments: name, [is_active]
    - job_titles: department_id, name, [is_active]
    - locations: name, [is_active]
    """
    return await _request("POST", _lookup_path(kind), json_body=fields)


@mcp.tool()
async def update_lookup(kind: str, lookup_id: int, fields: dict[str, Any]) -> Any:
    """Partially update a lookup record (PATCH) by id; returns the updated record.

    `kind` is one of: countries, states, statuses, departments, job_titles,
    locations. `fields` holds only the values to change (same field names as
    `create_lookup`).
    """
    return await _request(
        "PATCH", f"{_lookup_path(kind)}{lookup_id}", json_body=fields
    )


@mcp.tool()
async def delete_lookup(kind: str, lookup_id: int) -> Any:
    """Delete a lookup record by id. `kind` is one of: countries, states,
    statuses, departments, job_titles, locations. Fails if the record is still
    referenced by employees or other records (the app returns a 409).
    """
    return await _request("DELETE", f"{_lookup_path(kind)}{lookup_id}")


# ---------------------------------------------------------------------------
# Report tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def headcount_report(
    group_by: str = "department", include_archived: bool = False
) -> Any:
    """Employee headcount grouped by a dimension.

    `group_by` is one of: department, location, status, job_title, country.
    Returns per-group counts plus a total.
    """
    return await _get(
        "/api/v1/reports/headcount",
        {"group_by": group_by, "include_archived": include_archived},
    )


@mcp.tool()
async def org_report(limit: int = 50) -> Any:
    """Org-structure summary: managers ranked by span of control, plus rollups
    (total employees, managers, individual contributors, avg/max span).
    """
    return await _get("/api/v1/reports/org", {"limit": limit})


@mcp.tool()
async def activity_report(days: int = 7) -> Any:
    """Summary of audit/activity events over a trailing window of `days`,
    bucketed by category, event type, and outcome.
    """
    return await _get("/api/v1/reports/activity", {"days": days})


def main() -> None:
    """Run the MCP server over streamable HTTP, behind inbound gateway auth."""
    import uvicorn

    from mcp_server.gateway_auth import GatewayAuthMiddleware

    log.info(
        "hrsot_mcp_starting host=%s port=%s path=%s upstream=%s data_dir=%s",
        settings.bind_host,
        settings.bind_port,
        settings.path,
        settings.hr_api_base_url,
        settings.data_dir,
    )
    # Wrap FastMCP's streamable-HTTP ASGI app with our inbound bearer-auth check.
    # Non-HTTP scopes (lifespan) pass through so the session manager still starts.
    app = GatewayAuthMiddleware(mcp.streamable_http_app())
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
