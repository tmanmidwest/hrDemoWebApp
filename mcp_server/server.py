"""HR SoT MCP server — a streamable-HTTP gateway over the HR REST API.

Design
------
This server is a small, **stateless** proxy that exposes read-only MCP tools over
the HR REST API. It uses two credentials, both created and rotated in the app UI
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
        "Query the Demo HR Source-of-Truth system: list employees and lookups, "
        "and run aggregate headcount, org-structure, and activity reports. All "
        "tools are read-only."
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


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET the HR API with the server's service token; return parsed JSON.

    Raises ToolError with a helpful message on missing/invalid auth or an upstream
    error, so the model sees why a call failed instead of a raw stack.
    """
    token = _resolve_service_token()
    if not token:
        raise ToolError(
            "The MCP server has no API token to reach the app. An admin needs to "
            "generate one in the app UI (Settings → MCP → Generate API token), or "
            "set HRMCP_API_KEY / HRMCP_API_KEY_FILE for a remote host."
        )

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
            "The HR API rejected the MCP server's token (401). Rotate it in the "
            "app UI (Settings → MCP)."
        )
    if resp.status_code == 403:
        raise ToolError(
            "The MCP server's token lacks the scope required for this data (403)."
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
        },
    )


@mcp.tool()
async def get_employee(employee_id: int) -> Any:
    """Get a single employee (with nested department, title, status, location,
    supervisor) by numeric id.
    """
    return await _get(f"/api/v1/employees/{employee_id}")


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
