# Web UI

The Demo HR Source of Truth App ships with a complete web UI for managing employees, lookup tables, admin users, API credentials, and the reset feature. This doc walks through the UI surface page by page and covers the design system and key interaction patterns.

## Accessing the UI

- **Root URL** (`/`) redirects to `/ui/employees`
- **Unauthenticated requests** to any `/ui/*` page redirect to `/ui/login?next=<original-url>`, then return you to the original page after login
- **Login page** is at `/ui/login`
- **The OpenAPI/Swagger docs** are at `/docs` and `/redoc` and are reachable without UI login (they use API authentication, not session cookies)

The UI runs on the same host and port as the REST API. There is no separate frontend deployment.

## Default credentials

| Field | Value |
|---|---|
| Username | `robbytheadmin` |
| Password | `N0nPr0dF0r$@viynt8` |

Change the password on first login via **Settings → Admin Users → Change Password**.

## Page-by-page walkthrough

### Login (`/ui/login`)

Standard username + password form. Form submits to `/ui/login` (POST). On success, sets a session cookie (`hrsot_session`, signed with a persisted secret, `SameSite=Lax`, 8-hour max age) and redirects to the original requested URL or to `/ui/employees`.

Wrong credentials re-render the login page with an inline error.

### Employees (`/ui/employees`)

The primary working surface. Shows a table of employees with the following features:

- **Tabs**: Active (default), All, Archived. The "Archived" tab is the only place archived employees appear by default.
- **Sortable columns**: Click any header marked with a sort indicator (Employee #, Name, Hire Date) to toggle ascending/descending.
- **Default sort**: Active-status employees always appear before non-active per the spec, regardless of the secondary sort column.
- **Column visibility picker**: Click the "Columns ▾" button to show/hide Department, Job Title, Work Email, Supervisor, Hire Date, and Country. Selections are saved to your browser's localStorage under the key `hrsot.cols.employees` — they persist per machine, not per user.
- **Status badges**: Green dot for "Active" or any other `is_active_status=true` status; amber for non-active statuses (Not Active, Leave of Absence, Terminated); neutral gray "Archived" badge for archived rows.
- **Row actions**: Edit, Archive (or Restore if currently archived).

### Add/Edit Employee (`/ui/employees/new` and `/ui/employees/{id}/edit`)

A single form template handles both create and edit. Sections:

1. **Identity** — employee number, first/middle/last name
2. **Address** — street address, city, country, state/province, postal code
3. **Contact** — work email, personal email, home phone
4. **Employment** — employment status, cost center, department, job title, location (optional), hire date, termination date, supervisor

Two HTMX-powered dependent dropdowns:

- **Country → State/Province**: When you pick a country, an HTMX request fetches `/ui/employees/_states-options?country_id=X` and replaces the state dropdown's options inline. No full page reload.
- **Department → Job Title**: Same pattern via `/ui/employees/_job-title-options?department_id=X`.

The **Supervisor** dropdown is populated server-side with only currently-active, non-archived employees. When editing an existing employee, that employee is excluded from the list (no self-supervision).

Validation errors (invalid FK combinations, duplicate employee number, etc.) re-render the form with an inline error at the top and all field values preserved.

### Lookup Management (`/ui/lookups/...`)

Six lookup pages, all following the same pattern:

- **Countries** (`/ui/lookups/countries`)
- **States/Provinces** (`/ui/lookups/states-provinces`)
- **Employment Statuses** (`/ui/lookups/employment-statuses`)
- **Departments** (`/ui/lookups/departments`)
- **Job Titles** (`/ui/lookups/job-titles`)
- **Locations** (`/ui/lookups/locations`)

Each page has:

- A list of all rows with a Status column showing `Active`, `Inactive`, or `System` (where applicable)
- An "+ Add" button leading to a per-type form
- Edit and Delete row actions

**Reference-protected deletes**: If you try to delete a row that other rows still reference (e.g. delete the "United States" country while employees live there), the delete is blocked with a flash error like:

> Cannot delete United States: still referenced by 2 employee(s) and 51 state(s)/province(s). Deactivate it instead.

The recommended pattern is to set `is_active = false` rather than delete — that keeps the row out of new dropdowns without breaking existing employee records.

**System-flagged employment statuses** (`Active`, `Not Active`) cannot be deleted at all, and their numeric `value` cannot be changed (Saviynt and other IGA systems may depend on these values being stable). The delete button doesn't appear for system rows, and the value field is read-only in the edit form.

### Settings → Admin Users (`/ui/settings/admin-users`)

Manage the accounts that can log in to the UI.

- **List view**: shows username, status (Active/Disabled), created date, last login. The seeded `robbytheadmin` is marked with a "Seeded" badge.
- **Add Admin**: simple username + password form. Password must be at least 8 characters.
- **Change Password**: per-user, requires new password + confirm password. No "old password" check because admins can reset each other.
- **Delete**: blocked for the seeded `robbytheadmin` account (use Disabled instead) and for your own account.

### Settings → API Keys (`/ui/settings/api-keys`)

Manage long-lived bearer tokens for REST API consumers (Saviynt, scripts, integrations).

- **Create**: enter a name (e.g. "Saviynt Production Connector") and the system generates a new key formatted `hrsot_<32-random-chars>`. The **full key is displayed once** in a yellow banner at the top of the list page right after creation. The page warns: "This is the only time the full key is shown. After leaving this page you'll only see the prefix."
- **List**: shows name, prefix (`hrsot_AbCdEfGh…`), status (Active/Revoked), created date, last used.
- **Revoke**: marks the key with a `revoked_at` timestamp. Existing usage stops working immediately. The key remains in the list for audit purposes.
- **Delete**: permanently removes the key record.

Storage: only the prefix and SHA-256 hash of the full key are persisted. The plaintext value is never written to the database.

### Settings → OAuth Clients (`/ui/settings/oauth-clients`)

Same pattern as API keys but for OAuth 2.0 Client Credentials flow.

- **Create**: enter a name. The system generates a `client_id` (formatted `hrsot_client_<16-random>`) and `client_secret` (a separate random value). **Both values are displayed once** after creation, then only the `client_id` is visible.
- **Revoke**: stops new token issuance. Already-issued JWTs remain valid until they expire naturally (default 1 hour).
- **Delete**: permanently removes the client record.

To use these credentials, the consuming system calls `POST /oauth/token` with `grant_type=client_credentials`, the `client_id`, and the `client_secret`, and receives a JWT to use as a Bearer token on subsequent API calls.

### Settings → Reset Data (`/ui/settings/reset`)

Destructive operation for returning the demo to a clean state. The page is designed to make accidental clicks impossible:

1. **Checkboxes per table**: Employees, Employment Statuses, Departments & Job Titles, Locations, States/Provinces, Countries. Each checkbox has a description explaining what gets wiped and what gets re-seeded.
2. **Dependency validation**: Resetting any table that other tables reference requires the dependent tables to also be selected. For example, you can't reset Countries without also resetting States and Employees, because they'd be left with dangling FK references. The page enforces this server-side and shows a clear flash error if you submit an invalid combination.
3. **Typed-phrase confirm**: Below the checkboxes is an input field. You must type the word `RESET` (exactly) before the destructive button is enabled. JavaScript watches the field and toggles the button's `disabled` state.
4. **Action**: Submitting the form wipes the selected tables (in the correct order to respect FK constraints) and re-seeds them with their default data. Employees are re-seeded as two sample employees in "Not Active" status.

Admin users, API keys, OAuth clients, and the session/JWT signing keys are **never** touched by reset.

The reset action is logged at WARNING level with the operator's username and the list of tables reset:

```json
{"level": "WARNING", "logger": "app.ui.settings_routes",
 "message": "ui_reset_completed",
 "by": "robbytheadmin",
 "actions": ["employees (2)", "employment statuses (4)"]}
```

## Design system

The UI uses a hand-crafted CSS design system in `app/static/app.css` — no Tailwind, no Bootstrap, no component framework. The aesthetic is refined-minimal, modeled on tools like Linear, Stripe Dashboard, and Notion settings.

### Typography

- **Body**: [Geist Sans](https://vercel.com/font) loaded from a CDN — Vercel's open-source font, distinctive without being weird, optimized for screen readability.
- **Monospace** (employee numbers, codes, dates, key prefixes): Geist Mono.
- **Headings**: Same Geist Sans with tight letter-spacing for hierarchy.

### Color palette

| Role | Token | Color |
|---|---|---|
| Primary (buttons, active nav, links) | `--primary` | Deep slate `#1e293b` |
| Success (Active status, success flash) | `--success` | Emerald `#047857` |
| Warning (Not Active status, warning flash) | `--warning` | Amber `#b45309` |
| Danger (delete, error flash) | `--danger` | Rose `#b91c1c` |
| Info | `--info` | Sky `#0369a1` |
| Surface | `--surface` | White `#ffffff` |
| Background | `--bg` | Warm gray `#fafaf9` |

All colors are defined as CSS variables in `:root`, so theming changes are a single-file edit.

### Layout

- **Sidebar** (232px wide, fixed): brand at the top, grouped nav links (Employees, Lookups, Settings, External), version + current user at the bottom
- **Topbar** (56px tall): breadcrumb on the left, current user + sign-out on the right
- **Main content area**: page header (title + subtitle + actions), then a card-based content layout

### Interaction patterns

- **Flash messages**: top-right toast notifications, color-coded by level (success / error / warning / info), auto-dismiss after 5 seconds, slide-in animation.
- **Forms**: vertical labels above inputs, 2-column grid on wider screens, required fields marked with a red asterisk, focus ring matching the primary color, inline error block at the top of the form on validation failure.
- **Buttons**: four variants — primary (slate filled), secondary (white outlined), ghost (transparent for tertiary actions), danger (rose for destructive). Two sizes: default and `--sm`.
- **Tables**: sticky headers, hover row highlight, archived rows are grayed out, columns marked with `data-col="..."` participate in the column-visibility picker.
- **Confirms**: irreversible actions (archive, delete, revoke) use a JavaScript `confirm()` dialog. Reset uses the typed-phrase pattern described above for extra safety.

### Iconography

The sidebar uses tiny inline SVG icons. The page favicon is also an inline SVG (a small filing cabinet). No external icon font is loaded — every glyph is either an SVG, a Unicode arrow, or text.

## How HTMX is used

HTMX is loaded once in `base.html` via CDN. It is used sparingly, only for the two dependent-dropdown interactions on the employee form. Everything else is plain HTML form posts and full-page navigation. This keeps the UI simple, reload-friendly, and easy to reason about — there's no client-side router, no JSON-fetching frontend, no React rehydration.

When HTMX is used, the server returns a small HTML fragment (e.g. `<option>` tags) and HTMX swaps it into the target element. The fragments live in `app/templates/employees/_state_options.html` and `_job_title_options.html`.

## Browser support

The UI uses no exotic CSS features. It is tested against current versions of Chrome, Firefox, Safari, and Edge. No IE11 support; no polyfills.

## Where the code lives

```
app/
  ui/                                 # UI routers and dependencies
    auth_routes.py                    # /ui/login, /ui/logout
    dependencies.py                   # require_ui_user + redirect-to-login handler
    employee_routes.py                # /ui/employees/*
    lookup_routes.py                  # /ui/lookups/*
    settings_routes.py                # /ui/settings/*
    flash.py                          # Flash message session helpers
    templating.py                     # Jinja2 templates registry
  templates/
    base.html                         # Sidebar + topbar shell
    login.html                        # No-sidebar login page
    _macros.html                      # Reusable sort_link, status_badge, active_badge
    employees/
      list.html
      form.html                       # Used for both new and edit
      _state_options.html             # HTMX partial
      _job_title_options.html         # HTMX partial
    lookups/
      list.html                       # Generic list template (all 5 lookup tables)
      country_form.html
      state_form.html
      status_form.html
      department_form.html
      job_title_form.html
    settings/
      admin_users.html
      admin_user_new.html
      admin_user_password.html
      api_keys.html
      api_key_new.html
      oauth_clients.html
      oauth_client_new.html
      reset.html
  static/
    app.css                           # The design system
    app.js                            # Flash auto-dismiss, column picker, modal, reset-confirm
```

All UI tests live in `tests/test_ui.py` — 24 tests covering rendering, auth redirects, form submission, the column picker, the reset feature, and the API key/OAuth client one-time-reveal pattern.
