# REST API

Interactive API documentation is auto-generated and available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the app is running.

> **Complete offline reference (no running instance needed).** The full, always-accurate spec — every endpoint, parameter, and request/response schema — is committed to this repo, generated from the code:
>
> | File | Use |
> |---|---|
> | [`docs/api.html`](api.html) | Open in a browser (double-click) for a `/docs`-style reference — **works fully offline**, renders with the vendored [`redoc.standalone.js`](redoc.standalone.js) and the spec inlined. |
> | [`docs/openapi.json`](openapi.json) / [`docs/openapi.yaml`](openapi.yaml) | The raw OpenAPI 3.1 spec — import into Postman/Insomnia, or feed to a client generator. |
>
> Regenerate after any API change with `python scripts/export_openapi.py` (see [below](#regenerating-this-reference)). The rest of *this* document is a curated, human-friendly guide to auth and common integration scenarios; `api.html` / `openapi.yaml` are the exhaustive list.

This document describes the API surface, authentication, conventions, and gives example requests for common integration scenarios.

## Base URL

```
http://<host>:8000/api/v1
```

All endpoints below are relative to this base URL.

## Authentication

The REST API supports two authentication methods. Pick whichever the calling system supports.

- **API keys** carry **scopes** — each key is granted only the permissions it needs (see [API key scopes](#api-key-scopes) below). A key without the required scope for an endpoint gets `403 Forbidden`.
- **OAuth 2.0 client-credentials** tokens currently have **full access** (equivalent to the `admin` scope). Scoping OAuth clients is a planned follow-up.

### Method 1: API Key

Send the API key in the `Authorization` header as a Bearer token:

```http
GET /api/v1/employees HTTP/1.1
Host: hr.example.com
Authorization: Bearer hrsot_a8f3d9e2c1b4e5f6g7h8i9j0k1l2m3n4
```

API keys are created via the web UI under **Settings → API Keys** (or `POST /api/v1/auth/api-keys/`). The full key value is shown only once at creation.

### API key scopes

Each key is granted a set of scopes. Endpoints require a specific scope; the wildcard `admin` scope satisfies any check.

| Scope | Grants |
|---|---|
| `employees:read` | List/view employees |
| `employees:write` | Create, update, archive, restore, terminate, reactivate employees |
| `lookups:read` | List/view all lookup tables |
| `lookups:write` | Create, update, delete lookup rows |
| `users:read` | List/view console accounts |
| `users:write` | Create, update, enable/disable console accounts |
| `reports:read` | Run aggregate reports (headcount, org, activity) |
| `backup:create` | Generate a backup |
| `admin` | Full access to every API-key-authorized endpoint |

**Presets** offered in the UI when creating a key:
- **Employee Management** → `employees:read employees:write lookups:read`
- **Read-Only (View All)** → `employees:read lookups:read users:read reports:read`
- **Reporting / MCP** → `employees:read lookups:read reports:read`
- **Full Admin** → `admin`

Create a scoped key via the API (session-authenticated admin request):

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"name": "Saviynt Employee Sync", "scopes": ["employees:read", "employees:write", "lookups:read"]}' \
  "http://hr.example.com/api/v1/auth/api-keys/"
```

Omitting `scopes` creates a full-access (`admin`) key, for backward compatibility. Existing keys created before scopes were introduced default to `admin`.

### Method 2: OAuth 2.0 Client Credentials

Step 1 — exchange `client_id` and `client_secret` for a JWT bearer token:

```http
POST /oauth/token HTTP/1.1
Host: hr.example.com
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=hrsot_client_a1b2c3d4e5f6g7h8&client_secret=<secret>
```

Response:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

Step 2 — use the access token on subsequent requests:

```http
GET /api/v1/employees HTTP/1.1
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

OAuth clients are created via the web UI under **Settings → OAuth Clients**.

## Conventions

- All request and response bodies are JSON
- Timestamps are ISO-8601 UTC (e.g., `2026-05-18T14:30:00Z`)
- Dates without time are `YYYY-MM-DD`
- IDs are integers
- Pagination uses `?limit=<n>&offset=<n>`, default `limit=50`, max `limit=500`
- Errors return JSON with `{"detail": "<message>"}` and an appropriate HTTP status

## Endpoints

### Health

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Liveness + DB check |

### Employees

Required scope: `employees:read` for GET, `employees:write` for all mutations.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/employees` | List employees |
| GET | `/api/v1/employees/{id}` | Get one employee |
| POST | `/api/v1/employees` | Create employee |
| PUT | `/api/v1/employees/{id}` | Full update |
| PATCH | `/api/v1/employees/{id}` | Partial update |
| POST | `/api/v1/employees/{id}/archive` | Archive (soft-delete) |
| POST | `/api/v1/employees/{id}/restore` | Restore from archive |
| POST | `/api/v1/employees/{id}/terminate` | Set status to Terminated + termination date in one call |
| POST | `/api/v1/employees/{id}/reactivate` | Reverse a termination (status back to Active, clear date) |

**Query parameters on list endpoint**:
- `include_archived=true` — include archived employees (hidden by default)
- `employment_status_id=<id>` — filter by status
- `department_id=<id>` — filter by department
- `is_active_status=true|false` — filter by whether the assigned employment status is active
- `updated_since=<iso-datetime>` — incremental sync support
- `sort=<field>` and `order=asc|desc`

### Lookup Tables

Required scope: `lookups:read` for GET, `lookups:write` for POST/PUT/PATCH/DELETE.

All lookup tables support the same five operations.

| Resource | Path |
|---|---|
| Countries | `/api/v1/countries` |
| States/Provinces | `/api/v1/states-provinces` |
| Employment Statuses | `/api/v1/employment-statuses` |
| Departments | `/api/v1/departments` |
| Job Titles | `/api/v1/job-titles` |
| Locations | `/api/v1/locations` |

For each: `GET /` (list), `GET /{id}`, `POST /`, `PUT /{id}`, `DELETE /{id}`.

Some lookup rows are flagged `is_system=true` and cannot be deleted (returns 409 Conflict).

States/Provinces and Job Titles support filtering by parent:
- `GET /api/v1/states-provinces?country_id=<id>`
- `GET /api/v1/job-titles?department_id=<id>`

### Console Users

Manage the accounts that sign in to the web UI (create, read, update, enable/disable) and their roles. **There is no delete over the API** — disable instead.

Required scope: `users:read` for GET, `users:write` for all mutations.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/users` | List console accounts |
| GET | `/api/v1/users/{id}` | Get one account |
| POST | `/api/v1/users` | Create a local (password) account |
| PATCH | `/api/v1/users/{id}` | Update username, password, and/or role |
| POST | `/api/v1/users/{id}/disable` | Disable (account can no longer sign in) |
| POST | `/api/v1/users/{id}/enable` | Re-enable a disabled account |

`role` is one of `admin`, `management`, `view_only` (see [SCHEMA.md](SCHEMA.md#app-users-console-accounts)). The seeded admin cannot be disabled or demoted. Create example:

```bash
curl -X POST -H "Authorization: Bearer hrsot_..." \
  -H "Content-Type: application/json" \
  -d '{"username": "connector-svc", "password": "a-strong-secret", "role": "management"}' \
  "http://hr.example.com/api/v1/users"
```

### Credential management (API keys & OAuth clients)

Manage the credentials that authenticate REST callers. Admin scope required. These back the **Settings → API Keys** and **Settings → OAuth Clients** UI pages; secrets are returned **once** at creation and only a prefix is shown thereafter.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/auth/api-keys` | List API keys (prefix + metadata, never the full secret) |
| POST | `/api/v1/auth/api-keys` | Create a key with chosen scopes — full key returned once |
| GET | `/api/v1/auth/api-keys/{id}` | Get one key's metadata |
| DELETE | `/api/v1/auth/api-keys/{id}` | Delete a key |
| POST | `/api/v1/auth/api-keys/{id}/revoke` | Revoke a key (kept for audit, can no longer authenticate) |
| GET | `/api/v1/auth/oauth-clients` | List OAuth clients |
| POST | `/api/v1/auth/oauth-clients` | Create a client — `client_secret` returned once |
| GET | `/api/v1/auth/oauth-clients/{id}` | Get one client |
| DELETE | `/api/v1/auth/oauth-clients/{id}` | Delete a client |
| POST | `/api/v1/auth/oauth-clients/{id}/revoke` | Revoke a client |

### Session auth (web UI)

Cookie-based session login used by the browser UI (and available for scripted callers that prefer sessions over bearer tokens). See the [`docs/api.html`](api.html) reference for exact request/response shapes.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/auth/session/login` | Log in with username + password; sets the session cookie |
| POST | `/api/v1/auth/session/logout` | Clear the session |
| GET | `/api/v1/auth/session/me` | Current signed-in user |

### Backup

Required scope: `backup:create`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/backup` | Generate and download a full-instance backup zip |

Returns `application/zip` as an attachment. Supply an optional password to AES-256 encrypt the archive. **The backup contains the database _and_ this instance's secret keys** — treat it as a credential.

```bash
# Unencrypted
curl -X POST -H "Authorization: Bearer hrsot_..." \
  -o backup.zip "http://hr.example.com/api/v1/backup"

# Password-encrypted
curl -X POST -H "Authorization: Bearer hrsot_..." \
  -H "Content-Type: application/json" -d '{"password": "s3cret"}' \
  -o backup.zip "http://hr.example.com/api/v1/backup"
```

Restore is intentionally **not** exposed over the API — it is destructive and available only in the UI under **Settings → Backup & Restore**.

### Reports

Required scope: `reports:read`. Read-only aggregate views over employee and audit data. These also back the [MCP server](MCP.md)'s report tools.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/reports/headcount` | Employee counts grouped by a dimension |
| GET | `/api/v1/reports/org` | Managers by span of control + org rollups |
| GET | `/api/v1/reports/activity` | Audit-event counts over a trailing window |

**Headcount** — `?group_by=` one of `department` (default), `location`, `status`, `job_title`, `country`; `?include_archived=true` to count archived employees too. Employees with no value for a nullable dimension (e.g. no location) are counted in an `Unassigned` bucket. Buckets are ordered by count, descending.

```bash
curl -H "Authorization: Bearer hrsot_..." \
  "http://hr.example.com/api/v1/reports/headcount?group_by=department"
```

```json
{
  "group_by": "department",
  "include_archived": false,
  "total": 42,
  "buckets": [
    {"key": 1, "label": "Engineering", "count": 18},
    {"key": 3, "label": "Sales", "count": 12}
  ],
  "generated_at": "2026-07-15T14:25:34Z"
}
```

**Org** — `?limit=<n>` (default 50, max 500) caps the returned managers (largest span first). Rollups cover total employees, managers, individual contributors, employees without a supervisor, and average/max span.

**Activity** — `?days=<n>` (default 7, range 1–365) sets the trailing window. Results are bucketed by category, event type, and outcome. Bounded by the app's audit-retention window (`HRSOT_AUDIT_RETENTION_DAYS`, default 30).

### Supervisors

Supervisors are employees. To list employees eligible to be a supervisor:

```
GET /api/v1/employees?eligible_supervisor=true
```

This filters to non-archived employees with an active employment status. Optionally pass `&exclude_id=<id>` to exclude a specific employee (used by the edit form to prevent self-supervision).

## Example: Saviynt-style full employee sync

```bash
# Initial full pull
curl -H "Authorization: Bearer hrsot_..." \
  "http://hr.example.com/api/v1/employees?limit=500&offset=0"

# Incremental pull (subsequent runs)
curl -H "Authorization: Bearer hrsot_..." \
  "http://hr.example.com/api/v1/employees?updated_since=2026-05-17T00:00:00Z"

# Include terminated/archived employees for deprovisioning workflows
curl -H "Authorization: Bearer hrsot_..." \
  "http://hr.example.com/api/v1/employees?include_archived=true"
```

## Example: Create employee

```bash
curl -X POST -H "Authorization: Bearer hrsot_..." \
  -H "Content-Type: application/json" \
  -d '{
    "employee_number": "E10042",
    "first_name": "Jane",
    "last_name": "Doe",
    "work_email": "jane.doe@example.com",
    "country_id": 1,
    "employment_status_id": 1,
    "department_id": 3,
    "job_title_id": 12,
    "location_id": 2,
    "supervisor_id": 4,
    "hire_date": "2026-05-18"
  }' \
  "http://hr.example.com/api/v1/employees"
```

`location_id` is optional — omit it or send `null` to leave an employee with no location. It can be set or cleared later via `PATCH /api/v1/employees/{id}` with `{"location_id": <id>}` or `{"location_id": null}`.

## Example: IGA-friendly status writes

When changing employment status from an IGA platform like Saviynt, prefer the value-based write paths over raw PATCH on `employment_status_id`. Status `value` (e.g., `1`=Active, `0`=Not Active, `2`=Leave of Absence, `3`=Terminated) is the stable identifier across deployments; primary-key IDs are not.

**Change status only** (preferred over PATCH `employment_status_id`):

```bash
curl -X PATCH -H "Authorization: Bearer hrsot_..." \
  -H "Content-Type: application/json" \
  -d '{"employment_status_value": 2}' \
  "http://hr.example.com/api/v1/employees/42"
```

Sending both `employment_status_id` and `employment_status_value` in the same request returns 400.

**Terminate an employee** (atomic — sets status AND termination_date in one call):

```bash
# With explicit date
curl -X POST -H "Authorization: Bearer hrsot_..." \
  -H "Content-Type: application/json" \
  -d '{"termination_date": "2026-06-15"}' \
  "http://hr.example.com/api/v1/employees/42/terminate"

# With defaults — uses today's date and value=3 (Terminated)
curl -X POST -H "Authorization: Bearer hrsot_..." \
  "http://hr.example.com/api/v1/employees/42/terminate"
```

Both fields are optional. `termination_date` defaults to today (UTC) and must be on or after the employee's `hire_date`. `employment_status_value` defaults to `3` but can be overridden if your org uses custom statuses (e.g., voluntary vs involuntary termination).

**Reactivate an employee** (reverses a termination):

```bash
curl -X POST -H "Authorization: Bearer hrsot_..." \
  "http://hr.example.com/api/v1/employees/42/reactivate"
```

Defaults to `value=1` (Active) and clears `termination_date`. Pass `{"clear_termination_date": false}` to keep the historical date for reporting.

Both `/terminate` and `/reactivate` are idempotent and return 409 on archived employees — restore via `/restore` first if you need to update an archived record.

## Example response: Employee

```json
{
  "id": 42,
  "employee_number": "E10042",
  "first_name": "Jane",
  "middle_name": null,
  "last_name": "Doe",
  "address_line_1": null,
  "address_line_2": null,
  "city": null,
  "country": {"id": 1, "code": "US", "name": "United States"},
  "state_province": null,
  "postal_code": null,
  "home_phone": null,
  "personal_email": null,
  "work_email": "jane.doe@example.com",
  "cost_center": null,
  "employment_status": {"id": 1, "label": "Active", "value": 1, "is_active_status": true},
  "department": {"id": 3, "name": "Engineering"},
  "job_title": {"id": 12, "name": "Senior Engineer"},
  "location": {"id": 2, "name": "New York Office"},
  "supervisor": {"id": 4, "employee_number": "E10001", "first_name": "Sam", "last_name": "Roberts"},
  "hire_date": "2026-05-18",
  "termination_date": null,
  "is_archived": false,
  "created_at": "2026-05-18T14:30:00Z",
  "updated_at": "2026-05-18T14:30:00Z"
}
```

Lookup fields are returned as **nested objects** rather than bare IDs. This makes the API more useful for IGA systems that map directly to user attributes without needing secondary lookups.

For write operations (POST/PUT/PATCH), send only the FK ID (e.g., `"country_id": 1`).

## Errors

| Status | Meaning |
|---|---|
| 400 | Validation error (malformed body, invalid FK reference) |
| 401 | Missing or invalid auth |
| 403 | Auth valid but action not allowed |
| 404 | Resource not found |
| 409 | Conflict (duplicate `employee_number`, attempt to delete a system lookup row) |
| 422 | Pydantic validation failure (detailed field-level errors) |
| 500 | Server error |

## Regenerating this reference

The committed spec files (`docs/openapi.json`, `docs/openapi.yaml`) and the rendered `docs/api.html` are generated from the code — they are not hand-maintained. Regenerate them whenever the API surface changes:

```bash
python scripts/export_openapi.py
```

This rewrites all three files from the current routes. `api.html` renders with the vendored `docs/redoc.standalone.js` (a one-time ~0.9 MB asset), so it stays fully offline. A test (`tests/test_openapi_docs.py`) fails if the committed `openapi.json` drifts from the code, so CI catches a forgotten regeneration.

To refresh the vendored Redoc bundle itself (rarely needed):

```bash
curl -sSL -o docs/redoc.standalone.js \
  https://cdn.jsdelivr.net/npm/redoc@2.5.0/bundles/redoc.standalone.js
```
