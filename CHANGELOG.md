# Changelog

All notable changes to the Demo HR Source of Truth App are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Database migrations run automatically on startup; all changes below are
backward-compatible — existing data and API keys keep working.

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
