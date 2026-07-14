# Requirements

## Purpose

Provide a lightweight, self-contained HR Source of Truth application for non-production POC use. Primary consumer is **Saviynt Identity Cloud** via REST API, but the API is generic enough for any IGA/IAM/HR integration testing.

## Implementation status

As of this writing, all items below are **implemented and tested**. The test suite (120 tests, all passing) covers models, seed data, authentication, REST API, and the web UI.

| Area | Status |
|---|---|
| Database models, migrations, seed data | Implemented |
| Authentication (sessions, API keys, OAuth 2.0) | Implemented |
| REST API for employees and lookups | Implemented |
| Web UI (employees, lookups, settings, reset) | Implemented |
| Containerization and deployment | Implemented |

## Functional Requirements

### Employees

- Create, read, update employee records via UI and REST API
- Archive (soft-delete) employees — records remain in the database but hidden from default views and API responses
- Restore archived employees
- Filter employees by employment status, department, archived state
- Sort employees by any whitelisted column (id, employee_number, first_name, last_name, hire_date, termination_date, created_at, updated_at)
- Configurable column visibility in the employee list view (persisted per-machine in browser localStorage)
- Default sort order: active employees first, other statuses next, archived employees hidden (separate "Archived" view)
- Cross-FK validation: state must belong to country, job title must belong to department, no self-supervision, supervisor must be active and not archived
- Incremental sync via `updated_since` query parameter for IGA integrations
- Eligible-supervisor filter (`?eligible_supervisor=true&exclude_id=N`) for dropdown population

### Lookup Tables

The following lookup tables are managed via UI and exposed via REST:

- **Countries** — code (ISO-3166-1 alpha-2) and display name, ~70 seeded
- **States/Provinces** — display name and optional subdivision code, linked to a country, 80 seeded (US states, Canadian provinces, etc.)
- **Employment Statuses** — label, numeric value, `is_active_status` boolean flag, `is_system` flag (protects core statuses from deletion/value changes)
- **Departments** — display name, 8 seeded
- **Job Titles** — display name, linked to a department, 35 seeded
- **Locations** — display name, optional employee attribute, 8 seeded
- **Supervisor** — self-referential FK on employees table (rather than a separate managers table)

### Reference protection

- Deleting a lookup row that is still referenced by other rows returns HTTP 409 with a helpful message listing referencers; UI shows the same message via flash
- System-flagged employment statuses (Active, Not Active) cannot be deleted, and their numeric value cannot be changed (IGA integrations may depend on these specific values)

### Dependent Dropdowns

- State/Province dropdown filters by selected Country (HTMX-powered)
- Job Title dropdown filters by selected Department (HTMX-powered)
- Eligible-supervisor dropdown filters to active, non-archived employees

### Authentication

**Web UI**
- Session-cookie-based login (Starlette SessionMiddleware, signed cookies, 8-hour max age)
- Optional OIDC single sign-on (multiple identity providers; SSO users default to `view_only`)
- Seeded default admin: `robbytheadmin` / `N0nPr0dF0r$@viynt8`
- Multiple console users supported, created via UI, each with a role (`admin` / `management` / `view_only`) that gates UI access
- Password change via UI for any user; users can be enabled/disabled
- Reset script restores `robbytheadmin` password to the default without affecting other accounts
- Cannot delete, disable, or demote the seeded admin; cannot delete/disable your own account via UI

**REST API**
- API key authentication (`Bearer hrsot_<32-char-rand>` in `Authorization` header) with per-key least-privilege **scopes**
- OAuth 2.0 Client Credentials flow (`POST /oauth/token` returns JWT signed with persisted HS256 key; currently full access)
- Console-user management and backup export exposed over the API (users: no delete — disable instead; backup: export only)
- API keys and OAuth clients are created, viewed, revoked, and deleted via UI
- Secret values shown in full **only once** at creation, masked thereafter (only prefix is persisted in cleartext, full value is SHA-256 hashed)
- Each credential tracks created date, last used timestamp, last used IP, and (for keys) a name label

### Reset

- UI-driven reset page at `/ui/settings/reset` with checkboxes for each table
- Typed-phrase confirmation: the destructive button is disabled until the operator types `RESET` into a confirmation field
- Dependency validation: resetting countries requires also resetting states/provinces and employees; resetting employment statuses or departments requires resetting employees
- Employee table reset reloads 2 sample employees, all seeded with the "Not Active" employment status
- Lookup tables can be reset to default seed data
- Admin password reset is a separate command-line script (`python -m app.scripts.reset_admin_password`), not exposed in UI
- Admin users, API keys, and OAuth clients are never touched by reset

### Data Persistence

- SQLite database file stored in a mounted volume (`/data/hrsot.db`)
- Survives container restarts
- Initial admin credentials written to `/data/INITIAL_CREDENTIALS.txt` on first startup for operator reference
- Session signing key and JWT signing key both persisted to `/data/` so sessions and tokens survive restarts

## Non-Functional Requirements

### Deployment

- Single Docker container, single process, single exposed port (8000)
- Container image under 250MB (currently ~180MB)
- Startup time under 10 seconds with empty database
- Works locally via `docker run` or `docker compose up`
- Deployable to AWS ECS/Fargate, Azure Container Apps, Google Cloud Run, or Kubernetes without modification
- Configuration via environment variables (no config files required); all use `HRSOT_` prefix

### Performance

- Designed for single-instance use, POC-scale (under 10,000 employees)
- Response time under 500ms for typical employee list queries (SQLite, well within bounds at this scale)
- No horizontal scaling requirements

### Security

- Passwords hashed with bcrypt (passlib, cost factor 12)
- API key and OAuth secret values SHA-256 hashed in storage (never stored in plaintext)
- HTTPS termination assumed to be handled by upstream load balancer / reverse proxy in cloud deployments
- No PII protections beyond authentication — this is non-prod test data
- Sessions use `SameSite=Lax` cookies with `HttpOnly` flag
- No multi-tenancy (single-tenant by design)

### Observability

- Structured JSON logging
- `/health` endpoint returning app + database status
- Request logging includes auth method (session / API key / OAuth) and credential identifier (not the secret) — e.g. `principal: "api_key:hrsot_vutySBEf"`

### Maintainability

- Documentation-first development
- OpenAPI/Swagger spec auto-generated from FastAPI route definitions, served at `/docs` and `/redoc`
- Database migrations managed via Alembic
- Test coverage: 120 tests covering models, seed data, all REST API endpoints, all auth flows, and the UI

## Out of Scope

- Multi-tenancy
- High availability / clustering
- SCIM 2.0 protocol compliance (REST API is custom, not SCIM)
- SAML / OIDC SSO for the UI
- Audit log retention or compliance reporting
- Production-grade encryption at rest
- LDAP / Active Directory sync
- Email notifications
- File attachments on employee records
