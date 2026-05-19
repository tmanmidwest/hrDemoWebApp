# Saviynt Identity Cloud Integration

This document describes how to point Saviynt Identity Cloud at the Demo HR Source of Truth App as an HR data source.

## Use case

Saviynt typically pulls authoritative identity data from an HR system to drive its identity lifecycle (joiner / mover / leaver) workflows. This app serves that role for non-production POCs, demos, and integration testing.

## Managing the HR data

You can populate, edit, and reset the HR data this app exposes to Saviynt in two ways:

- **Web UI** at `/ui/employees` — best for interactive demos and ad-hoc edits. The reset feature at Settings → Reset Data lets you wipe and re-seed selected tables between demo runs.
- **REST API** at `/api/v1/...` — best for scripted data setup or integration with test harnesses. See [API.md](API.md) for the full endpoint list.

Both surfaces use the same database and the same validation rules, so changes are visible to Saviynt immediately regardless of which one made them.

## Connection model

| Aspect | Value |
|---|---|
| Connector type | REST (Web Services) |
| Protocol | HTTPS (via upstream load balancer) or HTTP for local testing |
| Authentication | API Key (Bearer) or OAuth 2.0 Client Credentials |
| Data direction | Read (Saviynt → HR API) |
| Sync model | Full or incremental (via `updated_since` query parameter) |

## Step-by-step setup

### 1. Generate credentials in the HR app

**Option A — API key**:

1. Log in to the web UI as an admin
2. Navigate to **Settings → API Keys**
3. Click **Create New API Key**
4. Name it (e.g., `Saviynt Production Connector`)
5. Copy the displayed key value — **this is the only time it will be shown in full**
6. Provide the key to whoever is configuring Saviynt

**Option B — OAuth 2.0 Client**:

1. Log in to the web UI as an admin
2. Navigate to **Settings → OAuth Clients**
3. Click **Create New OAuth Client**
4. Name it (e.g., `Saviynt Prod`)
5. Copy the `client_id` and `client_secret` — **the secret is shown only once**

### 2. Configure Saviynt connector

In Saviynt's connector configuration screen, set:

| Field | Value |
|---|---|
| Connector Type | REST |
| Base URL | `https://<your-hr-host>/api/v1` |
| Authentication Type | `Bearer Token` (for API key) or `OAuth2 Client Credentials` |

**For API key auth**:

| Field | Value |
|---|---|
| Authorization Header | `Bearer hrsot_<your-key>` |

**For OAuth auth**:

| Field | Value |
|---|---|
| Token Endpoint | `https://<your-hr-host>/oauth/token` |
| Client ID | `hrsot_client_<your-client-id>` |
| Client Secret | `<your-client-secret>` |
| Grant Type | `client_credentials` |
| Token Lifetime | `3600` (matches default) |

### 3. Configure the data import call

**Full sync** (initial load):

```
GET /api/v1/employees?limit=500&offset=0
```

Paginate with `offset` until response is empty.

**Incremental sync** (subsequent runs):

```
GET /api/v1/employees?updated_since=<last_sync_iso8601>
```

**Including terminated employees** (for deprovisioning):

```
GET /api/v1/employees?include_archived=true
```

### 4. Field mapping

Suggested mapping from HR fields to Saviynt user attributes. Adjust to match your Saviynt schema.

| HR field | Saviynt attribute |
|---|---|
| `employee_number` | `employeeid` |
| `first_name` | `firstname` |
| `middle_name` | `middlename` |
| `last_name` | `lastname` |
| `work_email` | `email` |
| `personal_email` | `email2` |
| `home_phone` | `phonenumber` |
| `department.name` | `departmentname` |
| `job_title.name` | `title` |
| `cost_center` | `costcenter` |
| `country.code` | `country` |
| `state_province.name` | `state` |
| `city` | `city` |
| `supervisor.first_name` + `supervisor.last_name` | `manager` (or map to a separate manager lookup) |
| `hire_date` | `startdate` |
| `termination_date` | `enddate` |
| `employment_status.value` | `statuskey` (1 = active, 0 = inactive) |
| `employment_status.is_active_status` | `statusvalue` (boolean for lifecycle decisions) |
| `is_archived` | Used to drive deprovisioning trigger |

### 5. Status-driven lifecycle

The `employment_status` object in each employee record returns both a numeric `value` and an `is_active_status` boolean. Use `is_active_status` for lifecycle decisions in Saviynt — it correctly handles statuses like "Leave of Absence" that have a non-zero status value but should keep the user active.

Suggested Saviynt rules:

| Condition | Action |
|---|---|
| `employment_status.is_active_status == true` AND `is_archived == false` | Provision / keep active |
| `employment_status.is_active_status == false` AND `is_archived == false` | Suspend |
| `is_archived == true` | Deprovision |

## Testing scenarios

The app supports manipulating data to test all the common Saviynt scenarios:

| Scenario | How to trigger |
|---|---|
| **Joiner** | Create a new employee via UI or `POST /api/v1/employees` |
| **Mover (dept change)** | PATCH `department_id` on an employee |
| **Mover (manager change)** | PATCH `supervisor_id` on an employee |
| **Leaver (terminated)** | PATCH `employment_status_id` to "Terminated" and set `termination_date` |
| **Leaver (archived)** | POST to `/api/v1/employees/{id}/archive` |
| **Rehire** | POST to `/api/v1/employees/{id}/restore`, then PATCH status back to Active |
| **Leave of Absence** | PATCH `employment_status_id` to "Leave of Absence" |
| **Status code change** | Add custom status via Settings → Employment Statuses |

## Resetting for clean test runs

Use the **Reset** page in the UI to wipe employees while keeping lookup data intact. After reset, the app reloads 1-2 sample employees (all in "Not Active" status) so the database is never completely empty — convenient for verifying the connector still authenticates and parses responses.

## Network considerations

- The app listens on port 8000 by default
- In cloud deployments, terminate HTTPS at the load balancer; the container itself speaks plain HTTP
- No outbound network calls are made by the app; Saviynt connects in
- IP allowlisting is not handled by the app — apply it at the network/firewall layer
- No CORS configuration is needed for server-to-server REST calls

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Unauthorized` on all calls | API key revoked, OAuth client revoked, or wrong header format |
| `401 Unauthorized` after working before | Token expired (OAuth) — Saviynt should auto-refresh; check token endpoint config |
| `404 Not Found` on `/api/v1/employees` | Base URL missing `/api/v1` prefix |
| Empty list when employees exist | Filter applied (`is_archived` defaults to hidden); try `?include_archived=true` |
| Slow responses | Check container resource limits; SQLite is fast but constrained by container CPU |
| `409 Conflict` on POST employee | Duplicate `employee_number` |
| `422 Unprocessable Entity` on POST | Required field missing or FK references invalid ID (e.g., `department_id` doesn't exist) |
