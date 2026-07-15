# Architecture

## Overview

The Demo HR Source of Truth App is a **single-process, single-container** application. The same Python/FastAPI process serves both the web UI (HTML) and the REST API (JSON), backed by a SQLite database file stored on a mounted volume.

This is the simplest architecture that satisfies the requirements. There is no separate API server, no separate frontend, no separate database container, no message queue, no cache layer. Every deployment target — local Docker, AWS, Azure, Kubernetes — runs the same single container.

**One optional add-on:** an [MCP server](MCP.md) (`hr-mcp`) can run as a second, **stateless** container. It is a thin gateway that exposes read-only Model Context Protocol tools to AI assistants and calls the app's REST API on their behalf. It holds no database handle — the app remains the single source of truth and the only writer. It uses two credentials, both managed in the app UI (Settings → MCP) and read from the app's data volume: its own outbound API key (to call the app) and a set of inbound gateway tokens (that clients present to it). It's entirely optional; the app runs identically with or without it.

## Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Single Container                         │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │             FastAPI / Uvicorn (port 8000)            │   │
│  │                                                      │   │
│  │  ┌──────────────┐  ┌─────────────┐  ┌────────────┐   │   │
│  │  │   UI Routes  │  │  REST API   │  │  Auth      │   │   │
│  │  │   (Jinja2)   │  │  (JSON)     │  │  (session, │   │   │
│  │  │              │  │             │  │   API key, │   │   │
│  │  │              │  │             │  │   OAuth)   │   │   │
│  │  └──────┬───────┘  └──────┬──────┘  └─────┬──────┘   │   │
│  │         │                 │               │          │   │
│  │         └─────────────────┴───────────────┘          │   │
│  │                          │                           │   │
│  │                  ┌───────┴────────┐                  │   │
│  │                  │  SQLAlchemy    │                  │   │
│  │                  │  (shared ORM)  │                  │   │
│  │                  └───────┬────────┘                  │   │
│  └──────────────────────────┼──────────────────────────-┘   │
│                             │                               │
│           ┌─────────────────┴───────────────┐               │
│           │      /data (mounted volume)     │               │
│           │  ├─ hrsot.db        (SQLite)    │               │
│           │  ├─ jwt_signing_key             │               │
│           │  └─ INITIAL_CREDENTIALS.txt     │               │
│           └─────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
        │                                            ▲
        │ HTTP :8000                                 │ HTTPS
        ▼                                            │
  ┌──────────┐                              ┌────────┴──────┐
  │ Operator │                              │ Saviynt /     │
  │ (Web UI) │                              │ IGA Connector │
  └──────────┘                              └───────────────┘
                                                    │
                                              (REST API consumer)
```

## MCP server (optional sidecar)

When enabled, the MCP server runs as a separate container beside the app. It holds no database and no baked-in secrets: it mounts the app's data volume read-only and reads its two credentials from there, both managed in the app UI (Settings → MCP).

```
  ┌──────────────┐   MCP (streamable HTTP)     ┌───────────────────────┐
  │  AI assistant│   POST /mcp  :8100          │  hr-mcp container      │
  │  (MCP client)│ ──────────────────────────► │  FastMCP gateway       │
  └──────────────┘   Authorization: Bearer     │                       │
                     hrsotgw_<gateway-token>    │  GatewayAuthMiddleware│
                            (inbound)           │  verifies inbound     │
                                                │  token vs synced file │
                                                └───────────┬───────────┘
                                                            │ outbound: its OWN
                                                            │ hrsot_ service key
                                                            │ (from mcp_api_key)
                                                            ▼
                                                ┌───────────────────────┐
                                                │  hr-sot container      │
                                                │  /api/v1/*  :8000      │
                                                │  (auth, scopes, audit) │
                                                └───────────────────────┘
             ▲ reads both token files from the shared /data volume (:ro):
             └── mcp_api_key (outbound key) · mcp_gateway_tokens.json (inbound hashes)
```

Two credentials, both created/rotated in Settings → MCP:

* **Inbound** — clients present a **gateway token** (`hrsotgw_…`); `GatewayAuthMiddleware` verifies its hash against `mcp_gateway_tokens.json`, which the app rewrites on every create/revoke. No token configured → the endpoint answers **503**; wrong token → **401**.
* **Outbound** — the server calls the REST API with its **own** `hrsot_` API key (from `mcp_api_key`), scoped to the read tools. So app calls are attributed to the "MCP Server" key, and the app enforces its scopes and writes its usual audit events.

The MCP tools are read-only (employee/lookup reads plus the aggregate `reports` endpoints).

## Why this shape

**Single container, single process**: Easiest possible deployment story. The promise is "one `docker run` and you have an HR system." Adding even one more container (a separate Postgres, an Nginx, a Redis) doubles the cognitive cost for the POC user.

**SQLite, not Postgres**: For an under-10K-employee single-instance POC, SQLite is faster than Postgres for reads, requires zero configuration, persists as a single file (trivially backup-able, copy-able, version-able), and removes a network hop. SQLAlchemy abstracts the database layer so swapping to Postgres later is a matter of changing a connection string and running migrations.

**Server-rendered UI, not SPA**: A React/Vue/Svelte frontend would require a Node build step, a separate static asset pipeline, CORS configuration, and would roughly triple the container size. Jinja2 + HTMX gets us dynamic dependent dropdowns, inline edits, and modal forms without any of that. The whole UI is HTML over the wire.

**REST API and UI share the same auth layer at the route level**: The UI uses session cookies; the API uses API keys and OAuth tokens. Both flow through the same FastAPI dependency injection system, so business logic stays consistent regardless of how the request was authenticated.

## Request Flow

### Web UI request

```
Browser → GET /employees
       → FastAPI route handler
       → Session cookie validated → app_user loaded
       → SQLAlchemy query
       → Jinja2 template render
       → HTML response
```

### REST API request (API Key)

```
Saviynt → GET /api/v1/employees
       → Authorization: Bearer hrsot_...
       → FastAPI route handler
       → API key hash lookup → API key record loaded → last_used_at/ip updated
       → SQLAlchemy query
       → Pydantic serialization
       → JSON response
```

### REST API request (OAuth)

```
Saviynt → POST /oauth/token (client_id + client_secret)
       → Validate client credentials
       → Issue JWT signed with /data/jwt_signing_key
       → Return access_token

Saviynt → GET /api/v1/employees
       → Authorization: Bearer <jwt>
       → JWT signature + expiration validated
       → Client ID extracted → OAuth client record loaded → last_used updated
       → SQLAlchemy query → JSON response
```

### MCP tool call (optional)

```
AI assistant → POST /mcp  (hr-mcp :8100)
       → Authorization: Bearer hrsotgw_...        (inbound gateway token)
       → GatewayAuthMiddleware verifies it vs mcp_gateway_tokens.json  (else 503/401)
       → FastMCP tool (e.g. headcount_report)
       → calls hr-sot /api/v1/reports/... with the server's OWN hrsot_ key
       → app validates key + scope (reports:read) → records audit event
       → SQLAlchemy aggregate query → JSON
       → returned to the assistant as the tool result
```

## Deployment Topologies

### Local Docker / Docker Compose

```
Operator's machine
  └─ Docker Engine
     ├─ demo-hr-sot container :8000
     │    └─ Named volume: hrsot-data → /data   (read-write)
     └─ demo-hr-mcp container :8100   (optional, stateless)
          ├─ HRMCP_HR_API_BASE_URL → http://hr-sot:8000
          └─ Same volume: hrsot-data → /data   (read-only, for the token files)
```

The MCP container is optional and stateless. Ports and container names are env-overridable (`HRSOT_HOST_PORT`/`HRMCP_HOST_PORT`, `HRSOT_CONTAINER_NAME`/`HRMCP_CONTAINER_NAME`) so multiple stacks can share one host — see [DEPLOYMENT.md](DEPLOYMENT.md#mcp-server-optional).

### AWS ECS Fargate

```
Internet
  └─ Application Load Balancer (HTTPS, ACM cert)
     └─ Target Group
        └─ ECS Service (Fargate, 1 task)
           └─ Container :8000
              └─ EFS mount → /data
```

### Azure Container Apps

```
Internet
  └─ Container App ingress (HTTPS managed)
     └─ Container App (single replica, min=1 max=1)
        └─ Container :8000
           └─ Azure Files volume → /data
```

### Kubernetes

```
Ingress (with TLS)
  └─ Service (ClusterIP)
     └─ Deployment (replicas=1)
        └─ Pod
           └─ Container :8000
              └─ PersistentVolumeClaim → /data
```

For **all cloud deployments**, persistent storage is non-optional. SQLite needs a volume that survives container restarts. The `/data` directory is the only state.

## Configuration

All configuration is via environment variables. None are required for a default deployment.

| Variable | Default | Purpose |
|---|---|---|
| `HRSOT_DATA_DIR` | `/data` | Where the SQLite DB and signing key live |
| `HRSOT_SESSION_SECRET` | Auto-generated | Cookie signing key |
| `HRSOT_LOG_LEVEL` | `INFO` | Logging verbosity |
| `HRSOT_INITIAL_ADMIN_PASSWORD` | `N0nPr0dF0r$@viynt8` | Override the seeded password |
| `HRSOT_BIND_HOST` | `0.0.0.0` | Host to bind |
| `HRSOT_BIND_PORT` | `8000` | Port to bind |

The optional MCP server has its own `HRMCP_`-prefixed variables (`HRMCP_HR_API_BASE_URL`, `HRMCP_BIND_PORT`, `HRMCP_PATH`, …). See the [DEPLOYMENT.md environment-variables table](DEPLOYMENT.md#environment-variables) and [MCP.md](MCP.md).

Unlike the app, the MCP server is stateless and **not** bound by the single-replica constraint below — it can be scaled horizontally since it holds no data.

## Scaling Considerations (Out of Scope for POC, Documented for Future)

If this app ever needed to scale beyond single-instance:

1. **Swap SQLite → Postgres**: Change the SQLAlchemy connection string. Run Alembic migrations against Postgres.
2. **External session store**: Move session storage from in-process to Redis.
3. **Stateless containers**: Once 1 and 2 are done, the container becomes stateless and can be horizontally scaled.
4. **JWT signing key**: Move from a file on disk to a mounted secret or KMS.

None of this is needed for the POC use case.

## Technology Choices Summary

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Standard, well-supported, FastAPI's home |
| Web framework | FastAPI | Auto-generated OpenAPI, async-first, fast |
| Server | Uvicorn | Production-ready ASGI server |
| ORM | SQLAlchemy 2.x | Industry standard, lets us swap DBs later |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| Database | SQLite | Zero-config, file-based, fast for this scale |
| UI templates | Jinja2 | Built into FastAPI ecosystem |
| UI interactions | HTMX | Server-rendered dynamic UI without a JS framework |
| Auth (UI) | itsdangerous-signed cookies | Built into Starlette |
| Auth (API key) | Custom dependency | Simple, hashed storage |
| Auth (OAuth) | python-jose for JWT | Standard library |
| Password hashing | bcrypt via passlib | Standard |
| Container base | python:3.12-slim | Small, secure |
