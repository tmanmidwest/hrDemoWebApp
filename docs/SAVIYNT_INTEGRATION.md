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
5. Choose the key's **permissions**. For a read-only employee sync, the **Read-Only (View All)** preset is enough; if the connector also writes back status/termination, use **Employee Management**. Avoid handing a connector a Full Admin key. (See [API.md → API key scopes](API.md#api-key-scopes).)
6. Copy the displayed key value — **this is the only time it will be shown in full**
7. Provide the key to whoever is configuring Saviynt

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

**Static reference managers** are excluded from all of the calls above by design. A reference manager (e.g. `margaretmanager`) is a fixed stand-in supervisor you can tag on real employees without importing it as a user — so Saviynt never tries to provision or reconcile it. The connector still receives it as the `supervisor` object (with `employee_number`, `first_name`, `last_name`) on anyone who reports to it, so the `manager` attribute resolves correctly. If you ever need to pull the record itself, add `?include_reference_managers=true`. Enable and seed reference managers from **Settings → System** in the HR app. See [What's static and what syncs](#reference-managers) below.

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
| `location.name` (nullable) | `locationname` |
| `cost_center` | `costcenter` |
| `country.code` | `country` |
| `state_province.name` | `state` |
| `city` | `city` |
| `supervisor.employee_number` | `manager` (stable, space-free handle — e.g. `margaretmanager`) |
| `supervisor.first_name` + `supervisor.last_name` | `manager` display name (alternative to the handle above) |
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

<a id="reference-managers"></a>
## Static reference managers — what's static and what syncs

Some POC instances keep a few fixed users around for other use cases and want to tag them as the manager of newly-added employees without those fixed users being imported as syncable identities. That's what a **static reference manager** is for.

Enable the feature and create the seeded `margaretmanager` record from **Settings → System**. Any employee can also be marked "Static" from the employee edit form once the feature is on.

How a reference manager behaves:

| Surface | Reference manager (e.g. `margaretmanager`) |
|---|---|
| `GET /api/v1/employees` list (full & incremental sync) | **Hidden** — Saviynt never sees it as a user to provision/reconcile |
| CSV export, headcount & org reports | **Excluded** |
| `supervisor` object on employees who report to it | **Present** — `employee_number` (space-free handle), `first_name`, `last_name`, `is_reference_manager: true` |
| `GET /api/v1/employees/{id}` by id | **Returned** (fetchable directly) |
| `?include_reference_managers=true` on the list | **Returned** (explicit opt-in) |
| HR web UI employee list | **Shown**, badged "Static" |

Practical effect: map the `manager` attribute from `supervisor.employee_number`. Employees you tag with Margaret carry `manager = margaretmanager` through the sync, while Margaret herself is never created or updated on the Saviynt side.

## Testing scenarios

The app supports manipulating data to test all the common Saviynt scenarios. The IGA-friendly write paths (`/terminate`, `/reactivate`, and `employment_status_value` on PATCH) are preferred over raw PATCH on `employment_status_id` — they're atomic, semantically clear, and reference status by its stable numeric value rather than DB primary key.

| Scenario | How to trigger |
|---|---|
| **Joiner** | Create a new employee via UI or `POST /api/v1/employees` |
| **Mover (dept change)** | PATCH `department_id` on an employee |
| **Mover (manager change)** | PATCH `supervisor_id` on an employee |
| **Leaver (terminated)** | `POST /api/v1/employees/{id}/terminate` — sets status to Terminated AND termination_date in one atomic call. Body optional; defaults to today's date |
| **Leaver (archived)** | `POST /api/v1/employees/{id}/archive` |
| **Rehire** | `POST /api/v1/employees/{id}/reactivate` (status back to Active, clears termination_date). If the record was archived, `POST /restore` first |
| **Leave of Absence** | PATCH with `{"employment_status_value": 2}` |
| **Status code change** | Add custom status via Settings → Employment Statuses |

### Why prefer value-based writes

Saviynt and other IGA platforms store status as a stable numeric code, not a database primary key. `employment_status.value` (`1`=Active, `0`=Not Active, `2`=Leave of Absence, `3`=Terminated) is guaranteed stable across deployments and resets; `employment_status_id` is not. All three IGA-friendly write paths above resolve status by `value`, so connector configuration only needs to know the four canonical codes — never the database IDs.

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
