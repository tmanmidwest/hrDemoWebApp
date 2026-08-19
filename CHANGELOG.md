# Changelog

All notable changes to the Demo HR Source of Truth App are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Database migrations run automatically on startup; changes are backward-compatible
unless a **Breaking** section says otherwise.

## [1.8.0] — 2026-08-19

Roles are now a **single concept**. A console account's role — **View Only /
Management / Admin** — is both what enforces access in the UI *and* the
entitlement an IGA platform (e.g. Saviynt) governs. The separate "Access Roles"
catalog and its per-user assignments (which never actually changed anyone's
access) are gone.

### Breaking

- **Removed the access-role catalog and assignment API.** `POST/PATCH/DELETE
  /api/v1/roles`, `GET /api/v1/roles/assignments`, and the per-user
  `GET/POST/DELETE /api/v1/users/{id}/roles` endpoints no longer exist.
- **`GET /api/v1/roles` is now read-only** and returns the fixed three-role
  catalog — `{id, name, description}` where `id` is the stored role value
  (`admin` / `management` / `view_only`). It is always available regardless of
  who is assigned, so an IGA can import it as its entitlement list.
- **Dropped the `roles:write` scope.** A user's role is now governed by setting
  the single-valued `role` attribute via `PATCH /api/v1/users/{id}`
  (`users:write`). The **IGA / Provisioning** key preset is now
  `employees:read, users:read, users:write, roles:read`. Existing keys that
  carried `roles:write` simply lose that (now-unknown) scope.
- **Dropped the `roles` and `role_assignments` tables** (migration `0018`),
  along with the `Role`, `RoleAssignment`, and `AccessLevel` models and the
  descriptive `access_level` classification.

### Changed

- **Roles UI is now a read-only catalog.** Settings → **Roles** lists the three
  fixed roles and how many active accounts hold each — mirroring what an IGA
  imports. Role **assignment** happens where it always has: Settings →
  **Users** (add a user with a role, or change a user's role). The old
  Assignments and role-editor pages were removed.

### Added

- **User Governance page** (Admin → **User Governance**, `/ui/admin/governance`).
  The IGA counterpart to the employee Connector Schema: a single reference of
  everything needed to govern console **users** — the lifecycle operations
  (create, update, change role, disable/enable, list roles), the account
  attributes (with access/required/updatable flags), the role entitlement
  catalog, and the governance rules (disable-not-delete, seeded-admin
  protections, single-valued roles) — with **JSON** and **CSV** downloads to
  hand to a connector builder such as Saviynt. Admin-only; exports are audited.

## [1.7.2] — 2026-08-07

### Fixed

- **Static assets are cache-busted.** The stylesheet and script are now linked
  with a `?v=<mtime>` token, so a rebuilt image always serves fresh CSS/JS
  instead of a browser-cached copy (which could render a page unstyled). Profile
  page icons also carry explicit dimensions so they never balloon if styles are
  slow to load.

### Added

**Employee profile (read-only detail page)**
- Employee names in the roster now link to a **profile page**
  (`/ui/employees/{id}`): an identity header with an avatar, a per-person tinted
  banner, a status pill, and a quick-facts strip (location, supervisor, hire date
  with tenure, work email), followed by panelled sections — Contact, Personal,
  Address, Employment, and Custom attributes. The supervisor and emails are
  links. An **Edit** button jumps to the form. Managers/admins only.

### Changed

**Custom attributes are now editable on the employee form**
- The custom fields (e.g. imported `worker_type`, `company`) that previously
  showed as a read-only block on the edit page are now **real, typed inputs**
  (text / number / date / yes-no dropdown) on both the create and edit forms.
  Values are coerced and validated against the field registry; a blank clears
  the attribute. They're also editable when creating a new employee.

## [1.7.1] — 2026-08-07

### Added

**Exclude a department from the employees list**
- The Department column filter gains an **is / is not** toggle. Choosing
  "is not" + a department (e.g. *POC User*) hides that department from the list
  while still showing everyone else — including employees with no department
  assigned. The mode persists across sorting, paging, and the view tabs, and
  counts as an active filter only when it's actually excluding.

## [1.7.0] — 2026-08-07

### Added

**Data Import wizard — self-service, in-app bulk import for any customer file**
- A new admin-only **Data Import** section (`/ui/admin/import`) walks an operator
  through a five-step wizard — **Upload → Map columns → Resolve → Preview →
  Import** — to load an arbitrary customer HR extract without pre-converting it.
  - **Upload:** accepts Excel (`.xlsx`) and CSV directly (adds an `openpyxl`
    dependency to read Excel natively).
  - **Map columns:** each source column is auto-matched to a standard field, a
    custom attribute, the "split Last, First" name transform, or ignored — all
    editable in the browser. The mapping can be **saved as a reusable profile**
    per customer and re-applied to a refreshed file in one click.
  - **Resolve:** translate source codes to system values (e.g.
    `USA → United States`, `A → Active`) and, with the **auto-create missing
    lookups** toggle, create the departments, job titles, locations, and
    states/provinces the file needs but the system doesn't have yet. State codes
    match seeded ISO-3166-2 values by suffix (`AL` ↔ `US-AL`) so seeded states
    are reused rather than duplicated.
  - **Preview:** a dry-run New / Update / Error classification per row — nothing
    is written until confirmed.
  - **Import:** upserts by `employee_number`, creates the planned lookups and
    custom fields, resolves in-file supervisors, and records per-row plus
    summary events in the Activity log. Re-running the same file is idempotent.

**Connector schema export — live attribute catalog for external connectors**
- A new admin **Connector Schema** page (`/ui/admin/schema`) and API endpoint
  (`GET /api/v1/employees/schema`, scope `employees:read`) describe the employee
  attribute surface **as it exists in this instance** — so a Saviynt (or other)
  connector can be mapped without guessing at instance-specific fields.
- Covers every attribute the employee API returns: core fields, nested
  reference objects (`department.name`, `employment_status.value`,
  `supervisor.employee_number`, …), and — crucially — the instance's **live
  custom fields** (`custom_fields.<key>`), which the static OpenAPI spec can't
  enumerate. Each entry carries type, nullability, a description, and a live
  example value pulled from real data (SSN always masked).
- Also includes enumerations (employment status `value`/`label` pairs) and a
  masked sample record for end-to-end mapping tests. Downloadable as **JSON**
  (rich) or **CSV** (flat attribute catalog); both actions are audited.

**Custom fields — admin-defined employee attributes**
- Employees gain a `custom_fields` JSON bag described by a new
  `custom_field_definitions` registry (key, label, type, order, export flag).
  Custom attributes are **surfaced in the employee API** under `custom_fields`
  (so downstream systems like Saviynt get every attribute in one call), shown
  read-only on the employee edit page, and populated by the import wizard.

### Changed

- **Adding an employee now requires only number, name, and country.**
  `department`, `job title`, `employment status`, `hire date`, and `supervisor`
  are all optional and can be filled in later — the columns are nullable and the
  API/UI/MCP no longer require them. (The old "a new employee must have a
  supervisor unless the table is empty" rule is gone.) When a value *is* given it
  is still validated (e.g. a job title, when paired with a department, must
  belong to it). Employee lists use outer joins so records missing these fields
  still appear, headcount reports show an "Unassigned" bucket, and the employee
  API returns `null` for the nested objects.
- `employees.hire_date` is now **nullable** — bulk imports and source systems
  that don't carry a hire date no longer force a placeholder. The API, import,
  and UI validation were relaxed to match.
- The employee CSV template/import no longer requires `hire_date`.
- **Deleting a department is now safe and explicit.** If employees are still
  assigned to the department (or any of its job titles), the delete is blocked
  with a message to reassign them first — employees are never orphaned or
  cascade-deleted. If only job titles remain, a confirmation page lists them and
  deletes the department and its titles together on confirm.

### Fixed

- **Deleted default org data no longer reappears after a rebuild.** Default
  departments, job titles, and locations are now seeded only once (tracked by
  `app_config.org_defaults_seeded`); an admin's later deletions persist across
  restarts and image rebuilds. Restoring the defaults remains available via
  Settings → Reset. Existing installs are marked already-seeded on upgrade, so
  nothing is re-created.

## [1.6.0] — 2026-08-06

### Added

**MCP server can now manage employees and lookups (not just read)**
- The MCP server gains **write tools** alongside the existing read/report tools,
  so an AI assistant can drive the full employee lifecycle and manage reference
  data through MCP:
  - **Employees:** `create_employee`, `update_employee`, `archive_employee`
    (disable), `restore_employee`, `terminate_employee`, `reactivate_employee`.
  - **Lookups:** `create_lookup`, `update_lookup`, `delete_lookup` for
    departments, job titles, locations, countries, states, and employment
    statuses (`kind=` selects which).
- Every write goes through the same REST API as the UI, so all existing
  validation (FK checks, SSN uniqueness, supervisor rules, status transitions)
  and **audit logging** apply unchanged. The tools drop unset optional fields so
  omitted values fall back to server defaults instead of nulling columns, and
  they surface the app's own 400/409/422 messages back to the caller for
  correction.
- The MCP service key's scopes were widened to match:
  `employees:read/write`, `lookups:read/write`, `reports:read`. The write surface
  is **intentionally bounded to employees and lookups** — console-user
  management, API-key/OAuth administration, and backup (which contains secret
  keys) are deliberately **not** exposed as MCP tools.
- **Upgrade note:** an MCP key generated on ≤ 1.5.x holds read-only scopes.
  After updating, **rotate the key** under Settings → MCP (*Generate API token*)
  so it picks up the write scopes — otherwise the new tools return `403`. Remote
  hosts using a static `HRMCP_API_KEY` must supply a key carrying those scopes.
  For a strictly read-only deployment, point the server at a read-only API key
  instead. **Rebuild the MCP image** to ship the new tools.
- No schema change; existing data, API keys, and read-only integrations are
  unaffected.

## [1.5.1] — 2026-08-06

### Fixed

**MCP server container crash-looped on fresh builds (`mcp 2.0` incompatibility)**
- The MCP image dependency was pinned as `mcp>=1.28.0` with **no upper bound**.
  `mcp 2.0.0` (released 2026-07-28) is a major SDK rework that drops the
  `mcp.server.fastmcp` import path the server is written against, so any image
  built on/after that date pulled 2.x and died on startup with
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. Because the
  container never finished starting, it never bound its port — surfacing in
  Portainer as **"no published ports"** even though `HRMCP_HOST_PORT` was set
  correctly. The app container (`hr-sot`) was unaffected.
- Capped the dependency to `mcp>=1.28.0,<2.0` so builds resolve to the latest
  1.x release. **Rebuild the MCP image** to pick up the fix (an env-only stack
  update won't help — the bad wheel is baked into the image layer): in Portainer
  redeploy with **Re-pull image and redeploy**, or run
  `docker compose up -d --build hr-mcp`.
- No code, schema, or configuration change; existing data and API keys are
  unaffected.

## [1.5.0] — 2026-08-05

### Added

**Sort & filter every column on the Employees list**
- **Every column is now sortable** — Employee #, Name, Status, Department, Job
  Title, Work Email, Supervisor, Hire Date, Country, and Location — via the
  clickable header arrows (previously only Employee #, Name, and Hire Date).
  Related-column sorts (department, supervisor, etc.) order by the displayed
  name; active employees still group first.
- **Global search box** above the table matches across employee number, first/
  last name, and work/personal email in one query.
- **Per-column filters** in a new filter row under the headers: substring boxes
  for the text columns (Employee #, Name, Work Email), dropdowns for the
  categorical columns (Status, Department, Job Title, Supervisor, Country,
  Location), and a from/to date range for Hire Date.
- Search, filters, and sort all live in the **URL query string**, so a filtered
  view is shareable and bookmarkable, sorting a filtered list keeps the filters,
  and switching Active/All/Archived tabs preserves them. A **Clear filters**
  button appears whenever any filter is active. Blank controls are dropped on
  submit so URLs stay tidy.
- Read-only (view-only) users can sort and filter too; the filter row respects
  the existing show/hide **Columns** picker. No schema change.

## [1.4.0] — 2026-08-05

### Added

**Employee-number uniqueness check on create/edit**
- Adding (or editing) an employee now validates the **employee number** up
  front: if another record already uses that number, the form re-renders with
  a clear message naming the conflicting employee — instead of only failing at
  the database layer after submit.
- The check is **case-insensitive** and trims surrounding whitespace (so
  `E00001`, `e00001`, and `  E00001  ` all collide), and it matches against
  **archived records too**, so a number tied to a terminated employee can't be
  silently reused. On edit, an employee keeps its own number without colliding
  with itself.

**Admin cleanup: delete all archived employees**
- Admins get a **"Delete all archived"** button on the Employees list's
  **Archived** tab that permanently removes every archived (soft-deleted)
  employee in one action — for cleaning out records that will never be
  restored. It is guarded by a typed confirmation prompt and is **admin-only**
  (management and view-only users neither see the button nor can hit the route).
- The purge safely clears any lingering **supervisor** references to a deleted
  record first, so it never trips the self-referential foreign key, and it
  writes an `employee.purged_archived` **audit event** capturing who ran it and
  which employees were removed.
- No schema change — existing data, API keys, and integrations are unaffected.

## [1.3.0] — 2026-08-03

### Added

**Static reference managers**
- A new **static reference manager** — a stand-in supervisor record (the
  canonical example is `margaretmanager`) that you can tag as the manager on
  employees you add, without it being a syncable employee itself. Ideal for
  POC instances that keep a few fixed users around for other use cases.
- A flagged record is **hidden from every external/bulk read** — the API/MCP
  `GET /employees` list, the CSV export, and the headcount/org reports — so
  downstream systems (Saviynt, etc.) never try to provision or update it. It
  is opt-in-visible via `?include_reference_managers=true` on the list
  endpoint (and the matching MCP `list_employees` argument).
- It still **resolves as the `supervisor`** on anyone who reports to it,
  carrying `employee_number` (e.g. `margaretmanager`, no spaces) as the stable
  manager handle, and remains **fetchable by id** and **assignable** via the
  supervisor picker (`?eligible_supervisor=true`).
- In the app's own web UI the record **stays visible**, badged **"Static"**,
  so operators can see it at a glance.
- **Enable per instance** from **Settings → System**: a feature toggle plus a
  one-click **"Create static manager (Margaret)"** seed (Margaret Manager,
  El Segundo, CA, `margaretmanager@saviynt.com`). Any employee can also be
  marked static from the employee edit form once the feature is on.
- Backward-compatible migration `0012` adds `employees.is_reference_manager`
  (default false, so existing rows are unaffected) and the
  `app_config.reference_managers_enabled` toggle. `EmployeeOut` gains an
  `is_reference_manager` field.

## [1.2.0] — 2026-07-29

### Added

**Bulk employee CSV import / export**
- New **Import CSV** and **Export CSV** actions on the Employees list (employee
  managers only). Import supports adding *and* updating employees in one file.
- **Downloadable template** (`/ui/employees/import/template.csv`) with the full
  human-readable column set and an example row, plus a **current-roster export**
  (`/ui/employees/export.csv`) in the same shape so "export → edit → re-import"
  round-trips. Exports never include SSNs.
- The CSV speaks in **names, not IDs** — Department, Job Title, Country,
  Employment Status, State/Province, Location, and Supervisor (by
  `employee_number`) are resolved case-insensitively, reusing the existing
  cross-FK validation rules.
- **Preview before commit**: an uploaded file is parsed and every row classified
  as **New**, **Update**, or **Error** (with per-row reasons and, for updates, a
  list of the fields that will change). Nothing is written until confirmed.
- Error rows don't block the batch — you can fix and re-upload, or proceed and
  import only the valid rows (the errored ones are skipped and reported).
- On an update, a **blank cell means "leave unchanged"** (partial updates), and
  supervisors referenced elsewhere in the same file are linked after insert.
- Every import (per-row create/update + a batch summary) and every export is
  recorded in the **Activity Log**.

**In-repo API reference (no running instance needed)**
- `scripts/export_openapi.py` generates the OpenAPI spec straight from the code
  to **`docs/openapi.json`** and **`docs/openapi.yaml`**, plus **`docs/api.html`**
  — a `/docs`-style Redoc reference that renders **fully offline** (vendored
  `docs/redoc.standalone.js`, spec inlined; no CDN). A developer gets the
  complete, always-accurate API surface without deploying the app.
- `tests/test_openapi_docs.py` fails if the committed spec drifts from the code,
  so a forgotten regeneration is caught in CI.
- [API.md](docs/API.md) now points at the generated reference as the exhaustive
  list and documents the previously-undocumented credential-management (API
  keys, OAuth clients) and session-auth endpoints.

## [1.1.0] — 2026-07-15

### Changed

**MCP server auth — two-token model + UI management**
- The MCP server no longer forwards the caller's API key (pass-through). It now
  uses **two credentials**, both created and rotated from a new **Settings → MCP**
  page with no redeploy:
  - **Outbound** — the server's own API key (an `api_keys` row named "MCP Server",
    scoped to the read tools) that it uses to call the REST API. Written to
    `/data/mcp_api_key`; rotate/clear from the UI.
  - **Inbound** — named, individually revocable **gateway tokens** (new
    `mcp_gateway_tokens` table, `hrsotgw_` prefix) that clients present to the MCP
    server. Active hashes are synced to `/data/mcp_gateway_tokens.json`, which the
    server verifies against live via an ASGI middleware. Until one exists the MCP
    endpoint returns **503**; a wrong/missing token returns **401**.
- The MCP container now mounts the app's data volume **read-only** to read those
  two files (it still holds no database). `HRMCP_API_KEY` / `HRMCP_AUTH_TOKEN` env
  overrides cover remote hosts that can't share the volume.
- The MCP server's own key is flagged **MCP** and protected on the API Keys page
  (managed from the MCP page instead).
- New migration `0010_add_mcp_gateway_tokens`. Mirrors the design used in the
  POC-Tracker app. See [docs/MCP.md](docs/MCP.md).

## [1.0.0] — 2026-07-15

First stable release. Consolidates the employee source-of-truth, web UI, REST
API (scoped API keys + OAuth 2.0), OIDC SSO, audit/activity log, and — new in
this release — an MCP server and an aggregate reports API.

### Added

**MCP server (streamable HTTP)**
- A new, optional **MCP server** — a separate, stateless container (`hr-mcp`) that exposes
  read-only tools for querying HR data and running reports over the Model Context Protocol's
  streamable-HTTP transport. It is a thin gateway in front of the REST API: each tool forwards
  the caller's own API token to the app, so the app's existing **scopes and audit logging apply
  unchanged** and every tool call is attributed to a specific key.
- Tools: `list_employees`, `get_employee`, `list_lookups`, `headcount_report`, `org_report`,
  `activity_report`.
- Fully port-/name-configurable (`HRMCP_*` env vars, plus `HRMCP_HOST_PORT` /
  `HRMCP_CONTAINER_NAME` in Compose) so a dev and prod stack can coexist on one Docker host.
- See [docs/MCP.md](docs/MCP.md) for setup and client configuration.

**Reports API**
- New aggregate reporting endpoints under `/api/v1/reports/*`, gated by a new **`reports:read`**
  scope:
  - `GET /reports/headcount` — employee counts grouped by department, location, status, job
    title, or country (with an `Unassigned` bucket for nullable dimensions).
  - `GET /reports/org` — managers by span of control, plus org rollups (managers, individual
    contributors, employees without a supervisor, avg/max span).
  - `GET /reports/activity` — audit-event counts over a trailing window, bucketed by category,
    event type, and outcome.
- New API-key scope `reports:read` and a **Reporting / MCP** preset
  (`employees:read` + `lookups:read` + `reports:read`). The existing **Read-Only (View All)**
  preset now also includes `reports:read`. Running a report is itself an audited event.

### Notes

- No database migration is required — the reports read existing tables and `reports:read` is a
  scope string. Existing API keys keep working unchanged.

## [0.3.0] — 2026-07-14

### Added

**Dark mode**
- A light/dark theme toggle (sun/moon) in the topbar and on the login page. The whole UI is
  CSS-variable–driven, so dark mode is a full re-theme of surfaces, text, accents, semantic
  colors, and shadows.
- Theme is a **per-user profile preference** (`app_users.theme`): it saves to the signed-in
  account and follows the user across browsers and devices. When unset, the UI follows the
  operating system's `prefers-color-scheme`.
- The saved preference is rendered server-side onto `<html>` (plus a small pre-paint head
  script) so there is no flash of the wrong theme on load or navigation.
- New endpoint `POST /ui/preferences/theme` (any signed-in user) persists the choice;
  `system` clears it back to OS-follow.

### Migrations

- `0009_add_user_theme` — adds nullable `app_users.theme` (existing accounts follow the OS
  until they pick a theme).

## [0.2.0] — 2026-07-14

Four features landed in this batch: UI roles, backup/restore, a console-user &
backup REST API, and least-privilege scopes on API keys. Test suite: 220 passing.

### Added

**Console user roles (UI)**
- Every login account now has a role that governs the UI:
  - `admin` — full access, including Settings and lookup management
  - `management` — full employee CRUD; view (not manage) lookups and the activity log; no Settings
  - `view_only` — read-only employees and activity log
- Settings is collapsed behind a single **admin-only** sidebar link with a landing hub
  (`/ui/settings`); the sidebar and row actions adapt to the signed-in user's role.
- SSO/OIDC-provisioned accounts default to `view_only`.
- Enable/disable for accounts on **Settings → Users**, plus inline role changes.
- Guardrail: the seeded `robbytheadmin` is always `admin` and cannot be demoted or
  disabled; you cannot disable your own account — so there is always ≥1 active admin.

**Backup & Restore**
- **Settings → Backup & Restore** (admin only): export the whole instance to a `.zip`
  containing the database plus the on-disk secret keys, optionally AES-256 password-encrypted.
- Restore replaces the current database and secret keys from a backup (typed `RESTORE`
  confirmation; live engine rebuild + auto-migrate of older backups).
- ⚠️ A backup file contains secrets — treat it as a credential.

**Console-user & backup REST API**
- `GET/POST /api/v1/users`, `GET/PATCH /api/v1/users/{id}`,
  `POST /api/v1/users/{id}/disable`, `POST /api/v1/users/{id}/enable`.
  **No delete over the API** — disable instead.
- `POST /api/v1/backup` returns the backup zip (optional `{"password": "..."}` body).
  Export only; restore stays UI-only.

**Scoped API keys (least privilege)**
- API keys now carry permission **scopes**; each REST endpoint requires a specific scope,
  and a key without it gets `403`.
  - Scopes: `employees:read`, `employees:write`, `lookups:read`, `lookups:write`,
    `users:read`, `users:write`, `backup:create`, `admin` (wildcard).
  - Presets in the create UI: **Employee Management**, **Read-Only (View All)**, **Full Admin**.
- Scopes are selectable when creating a key (UI checkboxes/presets or the `scopes` field on
  `POST /api/v1/auth/api-keys/`) and shown as badges on the key list.

### Changed

- **Settings → Admin Users** is now **Settings → Users** (adds a Role column, role editing,
  and enable/disable).
- REST API authorization is no longer uniform: API-key access is governed by scopes.
  (OAuth 2.0 client-credentials tokens still have full access — per-client scoping is a
  planned follow-up.)
- Documentation refreshed: `README.md`, `docs/API.md`, `docs/UI.md`, `docs/SECURITY.md`,
  `docs/SCHEMA.md`, `docs/REQUIREMENTS.md`, `docs/SAVIYNT_INTEGRATION.md`.

### Migrations

- `0007_add_user_role` — adds `app_users.role` (existing accounts default to `admin`).
- `0008_add_api_key_scopes` — adds `api_keys.scopes` (existing keys default to `admin`, i.e.
  full access).

### Notes for testers

- API keys created before this batch are treated as `admin` (full access) automatically.
- OAuth 2.0 client tokens are **not** scoped yet — they retain full access.
- After a restore you may need to sign in again; a full app restart is recommended so the
  restored session-signing key takes effect.
- New dependency: `pyzipper` (AES-encrypted zip support for backups).

## [0.1.0] — 2026-06

Initial POC release: employee records, managed lookup tables, REST API for employee CRUD,
web UI, API key + OAuth 2.0 client-credentials auth, OIDC single sign-on, branding, activity
log, and reset-data. Deployable via Docker/Compose, Portainer, and AWS ECS Fargate.
