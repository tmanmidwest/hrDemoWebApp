# Database Schema

All tables use SQLite. Primary keys are integers unless noted. Timestamps stored as UTC ISO-8601 strings.

## Employees

The core table.

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | Auto-increment |
| `employee_number` | TEXT | Yes | Unique, indexed, the public-facing employee identifier |
| `first_name` | TEXT | Yes | |
| `middle_name` | TEXT | No | |
| `last_name` | TEXT | Yes | |
| `address_line_1` | TEXT | No | |
| `address_line_2` | TEXT | No | |
| `city` | TEXT | No | |
| `country_id` | INTEGER | Yes | FK → `countries.id` |
| `state_province_id` | INTEGER | No | FK → `states_provinces.id` (must belong to selected country) |
| `postal_code` | TEXT | No | |
| `home_phone` | TEXT | No | |
| `personal_email` | TEXT | No | |
| `work_email` | TEXT | No | |
| `cost_center` | TEXT | No | |
| `employment_status_id` | INTEGER | Yes | FK → `employment_statuses.id` |
| `department_id` | INTEGER | Yes | FK → `departments.id` |
| `job_title_id` | INTEGER | Yes | FK → `job_titles.id` (must belong to selected department) |
| `hire_date` | DATE | Yes | |
| `termination_date` | DATE | No | |
| `supervisor_id` | INTEGER | Yes | FK → `employees.id` (self-referential; cannot equal own `id`) |
| `is_archived` | BOOLEAN | Yes | Default `false`. Soft-delete flag |
| `archived_at` | DATETIME | No | Set when `is_archived` becomes true |
| `created_at` | DATETIME | Yes | Auto-set |
| `updated_at` | DATETIME | Yes | Auto-set on update |

**Validation rules**:
- `state_province_id`, if set, must reference a state belonging to `country_id`
- `job_title_id` must reference a job title belonging to `department_id`
- `termination_date`, if set, must be on or after `hire_date`
- `employee_number` is case-insensitive unique
- `supervisor_id` must reference an employee record other than this one (no self-supervision)
- `supervisor_id` must reference an employee whose current employment status `is_active_status == true` and who is not archived (enforced at creation/update time; existing assignments are preserved if a supervisor later becomes inactive)

## Countries

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `code` | TEXT | Yes | ISO-3166-1 alpha-2, unique (e.g., `US`, `CA`, `GB`) |
| `name` | TEXT | Yes | Display name |
| `is_active` | BOOLEAN | Yes | Default `true`. Inactive countries hidden from dropdowns but preserved for existing records |

Seeded with ~250 ISO countries on first deploy.

## States / Provinces

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `country_id` | INTEGER | Yes | FK → `countries.id` |
| `code` | TEXT | No | Optional subdivision code (e.g., `CA-ON`, `US-IL`) |
| `name` | TEXT | Yes | Display name |
| `is_active` | BOOLEAN | Yes | Default `true` |

Seeded with US states, Canadian provinces, and a few other common subdivisions. More can be added via UI.

## Employment Statuses

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `label` | TEXT | Yes | Display name (e.g., `Active`, `Leave of Absence`, `Terminated`) |
| `value` | INTEGER | Yes | Numeric value sent to integrating systems (e.g., `1`, `0`) |
| `is_active_status` | BOOLEAN | Yes | Whether this status counts as "currently employed" for list sorting and active-employee filters |
| `is_system` | BOOLEAN | Yes | If `true`, status cannot be deleted via UI (protects seeded defaults) |

**Seeded defaults**:

| Label | Value | is_active_status | is_system |
|---|---|---|---|
| Active | 1 | true | true |
| Not Active | 0 | false | true |
| Leave of Absence | 2 | true | false |
| Terminated | 3 | false | false |

## Departments

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `name` | TEXT | Yes | Unique display name |
| `is_active` | BOOLEAN | Yes | Default `true` |

**Seeded defaults**: Engineering, Sales, Marketing, Human Resources, Finance, Operations, IT, Customer Support.

## Job Titles

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `department_id` | INTEGER | Yes | FK → `departments.id` |
| `name` | TEXT | Yes | Display name |
| `is_active` | BOOLEAN | Yes | Default `true` |

Unique on (`department_id`, `name`). Seeded with 3-5 titles per default department.

## Supervisors

There is no separate supervisors table. The `employees.supervisor_id` column is a self-referential foreign key to `employees.id`.

In UI dropdowns, the supervisor list is populated from the employees table, filtered to:
- Employees whose current `employment_status.is_active_status == true`
- Employees where `is_archived == false`
- Excluding the employee currently being edited (no self-supervision)

Display format: `{first_name} {last_name} ({employee_number})`.

**Seeding consideration**: Because the first employee created cannot have a supervisor (none exist yet), the seed data and the employee creation API treat `supervisor_id` as conditionally required — required for all employees except when the employees table is empty (the first employee is allowed to have a null supervisor). After the first employee exists, `supervisor_id` is required for all subsequent creates.

## App Users (Admin Accounts)

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `username` | TEXT | Yes | Unique, case-insensitive |
| `password_hash` | TEXT | Yes | bcrypt hash |
| `is_seeded` | BOOLEAN | Yes | `true` for `robbytheadmin`, used by reset script |
| `is_active` | BOOLEAN | Yes | Default `true`. Inactive accounts cannot log in |
| `created_at` | DATETIME | Yes | |
| `last_login_at` | DATETIME | No | |

## API Keys

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `name` | TEXT | Yes | Human-readable label (e.g., "Saviynt Production Connector") |
| `key_prefix` | TEXT | Yes | First 8 chars of the key, shown in UI for identification |
| `key_hash` | TEXT | Yes | SHA-256 hash of the full key |
| `created_by_user_id` | INTEGER | Yes | FK → `app_users.id` |
| `created_at` | DATETIME | Yes | |
| `last_used_at` | DATETIME | No | |
| `last_used_ip` | TEXT | No | |
| `revoked_at` | DATETIME | No | Soft-revoke; revoked keys cannot authenticate |
| `expires_at` | DATETIME | No | Optional expiration |

Full key format: `hrsot_<32-char-random>`. Shown to user only at creation.

## OAuth Clients

| Column | Type | Required | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `name` | TEXT | Yes | Human-readable label |
| `client_id` | TEXT | Yes | Unique, generated (e.g., `hrsot_client_<16-char-random>`) |
| `client_secret_hash` | TEXT | Yes | SHA-256 hash of the secret |
| `created_by_user_id` | INTEGER | Yes | FK → `app_users.id` |
| `created_at` | DATETIME | Yes | |
| `last_used_at` | DATETIME | No | |
| `last_used_ip` | TEXT | No | |
| `revoked_at` | DATETIME | No | |
| `token_lifetime_seconds` | INTEGER | Yes | Default 3600 (1 hour) |

Issued JWTs are signed with an app-wide signing key stored in `/data/jwt_signing_key`. Generated on first startup if not present.

## Indexes

- `employees.employee_number` (unique)
- `employees.is_archived` (for filtering)
- `employees.employment_status_id` (for filtering)
- `employees.supervisor_id` (for finding direct reports)
- `employees.last_name, first_name` (for default sort)
- `app_users.username` (unique)
- `api_keys.key_hash` (unique)
- `oauth_clients.client_id` (unique)

## Seed Data

On first startup:
1. Create `app_users` row for `robbytheadmin` with default password
2. Populate `countries` with ISO list
3. Populate `states_provinces` with US/CA defaults
4. Populate `employment_statuses` with the four defaults above
5. Populate `departments` and `job_titles` with sample data
6. Create 2 sample employees: the first with no supervisor, the second supervised by the first. Both seeded with **Not Active** employment status.
7. Write `/data/INITIAL_CREDENTIALS.txt`
