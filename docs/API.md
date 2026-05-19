# REST API

Interactive API documentation is auto-generated and available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the app is running.

This document describes the API surface, authentication, conventions, and gives example requests for common integration scenarios.

## Base URL

```
http://<host>:8000/api/v1
```

All endpoints below are relative to this base URL.

## Authentication

The REST API supports two authentication methods. Pick whichever the calling system supports; both grant the same level of access.

### Method 1: API Key

Send the API key in the `Authorization` header as a Bearer token:

```http
GET /api/v1/employees HTTP/1.1
Host: hr.example.com
Authorization: Bearer hrsot_a8f3d9e2c1b4e5f6g7h8i9j0k1l2m3n4
```

API keys are created via the web UI under **Settings → API Keys**. The full key value is shown only once at creation.

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

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/employees` | List employees |
| GET | `/api/v1/employees/{id}` | Get one employee |
| POST | `/api/v1/employees` | Create employee |
| PUT | `/api/v1/employees/{id}` | Full update |
| PATCH | `/api/v1/employees/{id}` | Partial update |
| POST | `/api/v1/employees/{id}/archive` | Archive (soft-delete) |
| POST | `/api/v1/employees/{id}/restore` | Restore from archive |

**Query parameters on list endpoint**:
- `include_archived=true` — include archived employees (hidden by default)
- `employment_status_id=<id>` — filter by status
- `department_id=<id>` — filter by department
- `is_active_status=true|false` — filter by whether the assigned employment status is active
- `updated_since=<iso-datetime>` — incremental sync support
- `sort=<field>` and `order=asc|desc`

### Lookup Tables

All lookup tables support the same five operations.

| Resource | Path |
|---|---|
| Countries | `/api/v1/countries` |
| States/Provinces | `/api/v1/states-provinces` |
| Employment Statuses | `/api/v1/employment-statuses` |
| Departments | `/api/v1/departments` |
| Job Titles | `/api/v1/job-titles` |

For each: `GET /` (list), `GET /{id}`, `POST /`, `PUT /{id}`, `DELETE /{id}`.

Some lookup rows are flagged `is_system=true` and cannot be deleted (returns 409 Conflict).

States/Provinces and Job Titles support filtering by parent:
- `GET /api/v1/states-provinces?country_id=<id>`
- `GET /api/v1/job-titles?department_id=<id>`

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
    "supervisor_id": 4,
    "hire_date": "2026-05-18"
  }' \
  "http://hr.example.com/api/v1/employees"
```

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
