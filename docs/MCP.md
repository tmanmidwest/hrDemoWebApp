# MCP Server

The app ships an optional **MCP (Model Context Protocol) server** that lets an
AI assistant query HR data and run reports over a standard, tool-based
interface. It runs as a **separate container** and speaks the **streamable-HTTP**
transport.

## What it is

The MCP server is a thin, stateless **gateway** in front of the existing REST
API. It exposes a handful of read-only tools; each one forwards the caller's own
API token to the HR app and returns the result. It stores nothing and holds no
credentials of its own.

```
  MCP client (Claude, etc.)
        │   Authorization: Bearer hrsot_...
        ▼
  hr-mcp  (this server, :8100, /mcp)      ← stateless, no DB
        │   same bearer token, forwarded
        ▼
  hr-sot  (the app, :8000, /api/v1/*)      ← auth, scopes, audit, SQLite
```

Because auth and data both live in the HR app:

- **Scopes are enforced by the app**, per token, exactly as they are for any
  REST call. A token scoped `reports:read` can run reports but not read
  employees unless it also has `employees:read`.
- **Every tool call is audited** in the app's Activity log, attributed to the
  specific API key — so you can see which key ran which report, and when.

## Tools

All tools are read-only.

| Tool | Calls | Required scope |
|---|---|---|
| `list_employees` | `GET /api/v1/employees/` | `employees:read` |
| `get_employee` | `GET /api/v1/employees/{id}` | `employees:read` |
| `list_lookups` | `GET /api/v1/{lookup}/` | `lookups:read` |
| `headcount_report` | `GET /api/v1/reports/headcount` | `reports:read` |
| `org_report` | `GET /api/v1/reports/org` | `reports:read` |
| `activity_report` | `GET /api/v1/reports/activity` | `reports:read` |

`list_lookups(kind=...)` accepts: `countries`, `states`, `statuses`,
`departments`, `job_titles`, `locations`.

## The reports API

The report tools are backed by REST endpoints you can also call directly (bearer
token with `reports:read`):

- **`GET /api/v1/reports/headcount?group_by=department&include_archived=false`**
  — employee counts grouped by `department`, `location`, `status`, `job_title`,
  or `country`. Employees with no value for a nullable dimension (e.g. no
  location) are counted in an `Unassigned` bucket.
- **`GET /api/v1/reports/org?limit=50`** — managers ranked by span of control,
  plus rollups: total employees, managers, individual contributors, employees
  with no supervisor, and average/max span.
- **`GET /api/v1/reports/activity?days=7`** — audit-event counts over a trailing
  window, bucketed by category, event type, and outcome. (Bounded by the app's
  audit retention window.)

## Setup

### 1. Create an API key

In the app: **Settings → API Keys → New**. Give it a name (this is how you'll
tell keys apart later) and pick scopes. The **Reporting / MCP** preset grants
`employees:read`, `lookups:read`, and `reports:read` — a good default for a
read-only assistant. Copy the `hrsot_...` value; it's shown only once.

You can create as many keys as you like — one per assistant, per environment, or
per person — and revoke any of them individually from the same page. The
Activity log and each key's **last-used** timestamp show which are actually in
use.

### 2. Run the server

With Docker Compose (starts alongside the app):

```bash
docker compose up -d          # brings up hr-sot and hr-mcp
```

The MCP endpoint is then at `http://localhost:8100/mcp`.

### 3. Point your MCP client at it

Configure your client with the streamable-HTTP URL and an `Authorization`
header carrying the API key:

```json
{
  "mcpServers": {
    "hrsot": {
      "type": "http",
      "url": "http://localhost:8100/mcp",
      "headers": { "Authorization": "Bearer hrsot_your_key_here" }
    }
  }
}
```

## Configuration

The server is configured via `HRMCP_`-prefixed environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `HRMCP_HR_API_BASE_URL` | `http://hr-sot:8000` | Upstream HR app base URL |
| `HRMCP_BIND_HOST` | `0.0.0.0` | Bind host |
| `HRMCP_BIND_PORT` | `8100` | Bind port |
| `HRMCP_PATH` | `/mcp` | URL path for the streamable-HTTP endpoint |
| `HRMCP_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request timeout to the HR API |
| `HRMCP_LOG_LEVEL` | `INFO` | Log level |
| `HRMCP_SERVER_NAME` | `hrsot-mcp` | MCP server name |

### Running dev and prod on one host

Every port and container name is overridable, so a dev stack and a prod stack
can coexist on the same Docker host. Compose reads these (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `HRSOT_HOST_PORT` | `8000` | Host port for the app |
| `HRSOT_CONTAINER_NAME` | `demo-hr-sot` | App container name |
| `HRMCP_HOST_PORT` | `8100` | Host port for the MCP server |
| `HRMCP_BIND_PORT` | `8100` | MCP container port |
| `HRMCP_CONTAINER_NAME` | `demo-hr-mcp` | MCP container name |

Example — a second (dev) stack on different ports and names:

```bash
HRSOT_HOST_PORT=9000 HRSOT_CONTAINER_NAME=dev-hr-sot \
HRMCP_HOST_PORT=9100 HRMCP_CONTAINER_NAME=dev-hr-mcp \
docker compose -p hrsot-dev up -d
```

The MCP server reaches the app by its Compose **service name** (`hr-sot`) on the
project network, so it keeps working regardless of the container-name overrides,
and each Compose project is isolated on its own network.

## Running without Docker

```bash
pip install -r mcp_server/requirements.txt
HRMCP_HR_API_BASE_URL=http://localhost:8000 python -m mcp_server
```

## Security notes

- The server never sees or stores credentials beyond forwarding the caller's
  bearer token for the duration of a request. Give each key the **least**
  scope it needs.
- It is a read-only surface: no tool creates, updates, or deletes data.
- Put it behind TLS (a reverse proxy) before exposing it beyond localhost, the
  same as the app itself.
