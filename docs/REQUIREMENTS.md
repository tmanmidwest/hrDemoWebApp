# Requirements

## Purpose

Provide a lightweight, self-contained HR Source of Truth application for non-production POC use. Primary consumer is **Saviynt Identity Cloud** via REST API, but the API is generic enough for any IGA/IAM/HR integration testing.

## Functional Requirements

### Employees

- Create, read, update employee records via UI and REST API
- Archive (soft-delete) employees — records remain in the database but hidden from default views and API responses
- Restore archived employees
- Filter employees by employment status, department, archived state
- Sort employees by any column
- Configurable column visibility in the employee list view
- Default sort order: active employees first, other statuses next, archived employees hidden (separate view)

### Lookup Tables

The following lookup tables are managed via UI and exposed via REST:

- **Countries** — code (ISO-3166-1 alpha-2) and display name
- **States/Provinces** — display name, linked to a country
- **Employment Statuses** — label, numeric value, `is_active_status` boolean flag
- **Departments** — display name
- **Job Titles** — display name, linked to a department
- **Managers** — first name, last name (optionally linked to an employee record)

### Dependent Dropdowns

- State/Province dropdown filters by selected Country
- Job Title dropdown filters by selected Department

### Authentication

**Web UI**
- Session-cookie-based login
- Seeded default admin: `robbytheadmin` / `N0nPr0dF0r$@viynt8`
- Multiple admin users supported, created via UI
- Password change via UI for any admin user
- Reset script restores `robbytheadmin` password to the default without affecting other admin accounts

**REST API**
- API key authentication (Bearer token in `Authorization` header)
- OAuth 2.0 Client Credentials flow (`POST /oauth/token` returns JWT)
- API keys and OAuth clients are created, viewed, and revoked via UI
- Secret values shown in full **only once** at creation, masked thereafter
- Each credential tracks created date, last used timestamp, last used IP

### Reset

- UI-driven reset page with checkboxes for each table
- User selects which tables to wipe; lookup data and admin users are preserved unless explicitly selected
- Employee table reset reloads 1-2 sample employees, all seeded with an inactive employment status
- Lookup tables can be reset to default seed data
- Admin password reset is a separate command-line script for security (not exposed in UI)

### Data Persistence

- SQLite database file stored in a mounted volume (`/data/hrsot.db`)
- Survives container restarts
- Initial admin credentials written to `/data/INITIAL_CREDENTIALS.txt` on first startup for operator reference

## Non-Functional Requirements

### Deployment

- Single Docker container, single process, single exposed port (8000)
- Container image under 250MB
- Startup time under 10 seconds with empty database
- Works locally via `docker run` or `docker compose up`
- Deployable to AWS ECS/Fargate, Azure Container Apps, Google Cloud Run, or Kubernetes without modification
- Configuration via environment variables (no config files required)

### Performance

- Designed for single-instance use, POC-scale (under 10,000 employees)
- Response time under 500ms for typical employee list queries
- No horizontal scaling requirements

### Security

- Passwords hashed with bcrypt
- API key and OAuth secret values hashed in storage (never stored in plaintext)
- HTTPS termination assumed to be handled by upstream load balancer / reverse proxy in cloud deployments
- No PII protections beyond authentication — this is non-prod test data
- CSRF protection on UI forms
- No multi-tenancy (single-tenant by design)

### Observability

- Structured JSON logging
- `/health` endpoint returning app + database status
- Request logging includes auth method (session / API key / OAuth) and credential identifier (not the secret)

### Maintainability

- Documentation-first development
- OpenAPI/Swagger spec auto-generated from FastAPI route definitions
- Database migrations managed via Alembic
- Test coverage for REST API endpoints and auth flows

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
