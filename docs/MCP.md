# MCP Server

The app ships an optional **MCP (Model Context Protocol) server** that lets an AI
assistant query HR data and run reports over a standard, tool-based interface. It
runs as a **separate container** and speaks the **streamable-HTTP** transport.

## What it is

The MCP server is a small, stateless **gateway** in front of the REST API. It
exposes read-only tools; each one calls the app's REST API and returns the JSON.
It holds no database of its own — it reaches the app over HTTP and reads its two
credentials from files the app writes to the shared data volume.

```
  MCP client (Claude, Saviynt, …)
        │   Authorization: Bearer hrsotgw_...   (inbound gateway token)
        ▼
  hr-mcp  (this server, :8100, /mcp)            ← stateless, no DB
        │   Authorization: Bearer hrsot_...     (outbound service key)
        ▼
  hr-sot  (the app, :8000, /api/v1/*)           ← auth, scopes, audit, SQLite
```

### Two credentials, both managed in the app UI

Everything is configured under **Settings → MCP** — no redeploy, no secrets baked
into the container. The MCP server reads both credentials live from the shared
data volume, so rotating either takes effect on its next call.

| Credential | Direction | Purpose | Where it lives |
|---|---|---|---|
| **Outbound service token** | MCP server → app | The MCP server's own API key for calling the REST API. Granted only the read scopes its tools need. | An `ApiKey` row named "MCP Server", mirrored to `<data_dir>/mcp_api_key`. |
| **Inbound gateway tokens** | client → MCP server | Named, individually revocable bearer tokens that external clients present to reach the MCP server. | `mcp_gateway_tokens` table; active hashes synced to `<data_dir>/mcp_gateway_tokens.json`. |

Why two? The MCP server authenticates **to** the app (outbound), and clients
authenticate **to** the MCP server (inbound). Keeping them separate means you can
issue a distinct gateway token per consumer (and revoke just that one) without
touching the server's own access to the app.

Until at least one gateway token exists, the MCP HTTP endpoint **rejects every
request with 503** — so it's safe to deploy the container before configuring it.

## Tools

All tools are read-only.

| Tool | Calls |
|---|---|
| `list_employees` | `GET /api/v1/employees/` |
| `get_employee` | `GET /api/v1/employees/{id}` |
| `list_lookups` | `GET /api/v1/{lookup}/` |
| `headcount_report` | `GET /api/v1/reports/headcount` |
| `org_report` | `GET /api/v1/reports/org` |
| `activity_report` | `GET /api/v1/reports/activity` |

`list_lookups(kind=...)` accepts: `countries`, `states`, `statuses`,
`departments`, `job_titles`, `locations`. The report tools are backed by the
`/api/v1/reports/*` endpoints (see [API.md](API.md)).

## Setup

### 1. Start the stack

```bash
docker compose up -d          # brings up hr-sot and hr-mcp
```

The MCP endpoint is at `http://localhost:8100/mcp`. It answers `503` until you do
the next step.

### 2. Configure the two credentials (Settings → MCP)

In the app, go to **Settings → MCP**:

1. **Generate the outbound API token** — click *Generate API token*. This mints
   the MCP server's own key (scoped `employees:read` + `lookups:read` +
   `reports:read`) and writes it to the data volume. The server picks it up on its
   next call. Rotating revokes the old one immediately.
2. **Generate an inbound gateway token** — under *Gateway tokens*, name one per
   consumer (e.g. "Saviynt prod") and click *+ Generate token*. Copy the
   `hrsotgw_...` value; it's shown only once. Create as many as you like and
   revoke any individually.

The outbound key also appears on the **API Keys** page, flagged **MCP** and
protected there (revoke/rotate it from the MCP page instead).

### 3. Point your MCP client at it

Use a **gateway token** (`hrsotgw_...`) in the `Authorization` header:

```json
{
  "mcpServers": {
    "hr-sot": {
      "type": "http",
      "url": "http://localhost:8100/mcp",
      "headers": { "Authorization": "Bearer hrsotgw_your_token_here" }
    }
  }
}
```

The **Connect a Claude client** card on the Settings → MCP page shows ready-to-copy
snippets for Claude Desktop (via `npx mcp-remote`) and Claude Code.

## Configuration

The server is configured via `HRMCP_`-prefixed environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `HRMCP_HR_API_BASE_URL` | `http://hr-sot:8000` | Upstream HR app base URL |
| `HRMCP_DATA_DIR` | `/data` | Shared volume where the app writes the token files |
| `HRMCP_BIND_HOST` | `0.0.0.0` | Bind host |
| `HRMCP_BIND_PORT` | `8100` | Bind port (inside the container) |
| `HRMCP_PATH` | `/mcp` | URL path for the streamable-HTTP endpoint |
| `HRMCP_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request timeout to the app |
| `HRMCP_LOG_LEVEL` | `INFO` | Log level |

### Remote MCP host (can't see the volume)

If you run the MCP server somewhere it *can't* share the app's data volume, supply
the two credentials directly as env overrides instead of via the UI files:

| Variable | Replaces | Notes |
|---|---|---|
| `HRMCP_API_KEY` | the `mcp_api_key` file (outbound) | An `hrsot_` API key created in the app's API Keys page. |
| `HRMCP_AUTH_TOKEN` | `mcp_gateway_tokens.json` (inbound) | A single static bearer clients must present. |

### The shared volume (Compose)

`docker-compose.yml` mounts the app's data volume into `hr-mcp` **read-only**:

```yaml
  hr-mcp:
    volumes:
      - hrsot-data:/data:ro
    environment:
      HRMCP_DATA_DIR: /data
```

Both containers run as uid 1000, so the MCP server can read the `0600` token
files. It only ever reads them.

### Running dev and prod on one host

Ports and container names are overridable so stacks can coexist:

```bash
HRSOT_HOST_PORT=9000 HRSOT_CONTAINER_NAME=dev-hr-sot \
HRMCP_HOST_PORT=9100 HRMCP_CONTAINER_NAME=dev-hr-mcp \
docker compose -p hrsot-dev up -d
```

`HRMCP_HOST_PORT` sets the inbound (published) port; the container always listens
on 8100 internally. Each Compose project is isolated on its own network and its
own data volume, so its MCP server reads that project's tokens.

## Security notes

- The MCP server holds no long-lived secret in its image; both credentials come
  from the volume (or env overrides) and are read per request.
- Give the outbound key least privilege (it's scoped to the read tools by default).
- Issue a separate inbound gateway token per consumer so you can revoke one
  without disrupting the others. Revoking is immediate (the synced file is
  rewritten and re-read on the next request).
- It's a read-only surface: no tool creates, updates, or deletes data.
- Put it behind TLS (a reverse proxy) before exposing it beyond localhost.
