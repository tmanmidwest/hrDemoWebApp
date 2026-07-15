"""HR SoT MCP server — a streamable-HTTP gateway over the HR REST API.

Design
------
This server is a thin, **stateless** proxy. It exposes read-only MCP tools that
each forward the *caller's own* ``Authorization: Bearer`` token to the HR SoT
REST API and return the JSON response. It holds no service-account credentials
and no database handle of its own:

* The MCP client is configured with an ``hrsot_`` API key (created in the HR
  app's Settings → API Keys, scoped to e.g. ``reports:read`` + ``employees:read``).
* That token rides along on every tool call, so the HR app enforces the exact
  same scopes and records the exact same audit trail it would for any REST call.
  Attribution is per-key, not per-gateway.

Because auth and data both live in the HR app, running this next to it is safe:
scale it, restart it, or point it at a different HR instance without touching
the source of truth.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

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
        "Query the Demo HR Source-of-Truth system: list employees and lookups, "
        "and run aggregate headcount, org-structure, and activity reports. All "
        "tools are read-only. Requests are authorized with the API key the client "
        "was configured with."
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


def _bearer_from_ctx(ctx: Context) -> str | None:
    """Pull the Bearer token out of the inbound HTTP request, or None."""
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        return None
    header = request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


async def _get(ctx: Context, path: str, params: dict[str, Any] | None = None) -> Any:
    """Forward a GET to the HR API with the caller's token; return parsed JSON.

    Raises ToolError with a helpful message on missing/invalid auth or an
    upstream error, so the model sees why a call failed instead of a raw stack.
    """
    token = _bearer_from_ctx(ctx)
    if not token:
        raise ToolError(
            "No API token was provided. Configure this MCP server in your client "
            "with an 'Authorization: Bearer hrsot_...' header — an HR SoT API key "
            "(Settings → API Keys) scoped for the data you want (e.g. reports:read, "
            "employees:read, lookups:read)."
        )

    # Drop unset (None) params so we don't send empty query values.
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        resp = await _get_client().get(
            path, params=clean, headers={"Authorization": f"Bearer {token}"}
        )
    except httpx.RequestError as exc:
        raise ToolError(
            f"Could not reach the HR API at {settings.hr_api_base_url}: {exc}"
        ) from exc

    if resp.status_code == 401:
        raise ToolError(
            "The HR API rejected the token (401). The API key may be invalid, "
            "revoked, or expired."
        )
    if resp.status_code == 403:
        raise ToolError(
            "The API key lacks the scope required for this data (403). Grant the "
            "key the needed scope (e.g. reports:read) in the HR app."
        )
    if resp.status_code == 404:
        raise ToolError("Not found (404).")
    if resp.status_code >= 400:
        raise ToolError(f"HR API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Employee tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_employees(
    ctx: Context,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
    department_id: int | None = None,
    employment_status_id: int | None = None,
    updated_since: str | None = None,
    sort: str = "last_name",
    order: str = "asc",
) -> Any:
    """List employees, with optional filtering, sorting, and pagination.

    `updated_since` is an ISO-8601 datetime for incremental views. Archived
    (soft-deleted) employees are excluded unless `include_archived` is true.
    Requires the `employees:read` scope on the API key.
    """
    return await _get(
        ctx,
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
        },
    )


@mcp.tool()
async def get_employee(ctx: Context, employee_id: int) -> Any:
    """Get a single employee (with nested department, title, status, location,
    supervisor) by numeric id. Requires the `employees:read` scope.
    """
    return await _get(ctx, f"/api/v1/employees/{employee_id}")


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
async def list_lookups(ctx: Context, kind: str) -> Any:
    """List reference/lookup records used across employee data.

    `kind` is one of: countries, states, statuses, departments, job_titles,
    locations. Requires the `lookups:read` scope on the API key.
    """
    path = _LOOKUP_PATHS.get(kind)
    if path is None:
        raise ToolError(
            f"Unknown lookup kind '{kind}'. Choose one of: "
            + ", ".join(sorted(_LOOKUP_PATHS))
        )
    return await _get(ctx, path)


# ---------------------------------------------------------------------------
# Report tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def headcount_report(
    ctx: Context, group_by: str = "department", include_archived: bool = False
) -> Any:
    """Employee headcount grouped by a dimension.

    `group_by` is one of: department, location, status, job_title, country.
    Returns per-group counts plus a total. Requires the `reports:read` scope.
    """
    return await _get(
        ctx,
        "/api/v1/reports/headcount",
        {"group_by": group_by, "include_archived": include_archived},
    )


@mcp.tool()
async def org_report(ctx: Context, limit: int = 50) -> Any:
    """Org-structure summary: managers ranked by span of control, plus rollups
    (total employees, managers, individual contributors, avg/max span).
    Requires the `reports:read` scope.
    """
    return await _get(ctx, "/api/v1/reports/org", {"limit": limit})


@mcp.tool()
async def activity_report(ctx: Context, days: int = 7) -> Any:
    """Summary of audit/activity events over a trailing window of `days`,
    bucketed by category, event type, and outcome. Requires the `reports:read`
    scope.
    """
    return await _get(ctx, "/api/v1/reports/activity", {"days": days})


def main() -> None:
    """Run the MCP server over streamable HTTP."""
    log.info(
        "hrsot_mcp_starting host=%s port=%s path=%s upstream=%s",
        settings.bind_host,
        settings.bind_port,
        settings.path,
        settings.hr_api_base_url,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
