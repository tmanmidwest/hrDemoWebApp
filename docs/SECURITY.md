# Security

> **This app is intended for non-production POC use only.** It is not hardened for handling real PII, real credentials, or production traffic.

## Threat model and scope

The app is designed for:
- Internal POC and demo environments
- Integration testing where the consuming system is trusted
- Local developer machines or sandboxed cloud accounts

It is **not** designed for:
- Public internet exposure without a hardened reverse proxy in front
- Storage of real employee PII
- Compliance-sensitive contexts (HIPAA, GDPR enforcement, SOC2 boundaries)
- Multi-tenant SaaS deployment

## Authentication

### Web UI

- Session-cookie-based auth using Starlette's signed session middleware
- Session cookies signed with `HRSOT_SESSION_SECRET` (auto-generated and persisted on first startup)
- Sessions expire after 8 hours of inactivity
- Passwords stored as bcrypt hashes (work factor 12)
- No password complexity enforcement (POC) — recommended to set strong passwords manually
- No MFA support
- No account lockout on failed attempts (POC tradeoff for ease of testing)
- Optional OIDC single sign-on: identity providers can be configured under **Settings → Identity Providers**; SSO-provisioned accounts default to the `view_only` role

**UI authorization roles** — each console account has a role that gates what it can do:
- `admin` — full access, including Settings and lookup management
- `management` — full employee CRUD; can view (not manage) lookups and the activity log; no Settings
- `view_only` — read-only access to employees and the activity log

The seeded `robbytheadmin` is always `admin` and cannot be demoted or disabled, guaranteeing at least one admin exists (no lockout).

### REST API

**API Key**:
- Format: `hrsot_<32-character-random-base62>`
- Stored as SHA-256 hash; only the prefix (first 8 chars) and hash are kept
- Full key value displayed exactly once at creation
- No expiration by default (optional via `expires_at`)
- Revocable from the UI
- **Scoped** — each key is granted least-privilege scopes (e.g. `employees:read`, `users:write`, `admin`); a key without the required scope for an endpoint gets `403`. See [API.md](API.md#api-key-scopes). Keys predating scopes default to `admin` (full access).

**OAuth 2.0 Client Credentials**:
- Standard `client_credentials` grant flow per RFC 6749
- Access tokens are JWTs signed with HS256
- Default token lifetime: 3600 seconds (1 hour)
- Signing key stored at `/data/jwt_signing_key` (auto-generated on first startup, 256 bits of entropy)
- Client secrets stored as SHA-256 hashes; shown in full only at creation
- Revoking an OAuth client invalidates the ability to mint new tokens but does **not** invalidate already-issued tokens until they expire (consistent with standard OAuth semantics)
- OAuth-client tokens currently have **full access** (equivalent to the `admin` scope); per-client scoping is a planned follow-up

### Backup / Restore

- **Settings → Backup & Restore** (admin only) exports a `.zip` of the database plus the on-disk secret keys (session secret, JWT signing key, provider secret key), so it restores as a faithful clone. **The backup file is itself a credential** — anyone holding it (and its password, if set) can stand up a copy of the instance with all secrets. Store it securely.
- Backups can be AES-256 encrypted with a password (via `pyzipper`). Password-less backups are plain zips.
- The same export is available over the API to keys holding the `backup:create` scope (`POST /api/v1/backup`). **Restore is UI-only** — it is destructive and replaces all data.

## Credential management

### Initial admin credentials

On first startup, the app creates an `app_users` row for `robbytheadmin` with the password `N0nPr0dF0r$@viynt8` (or whatever is set via `HRSOT_INITIAL_ADMIN_PASSWORD`).

The credentials are also written to `/data/INITIAL_CREDENTIALS.txt` for operator reference. **Delete this file after initial setup.**

### Changing passwords

Any admin can change their own password through the UI. Admins can also reset other admins' passwords (POC simplification — in production this would be more restricted).

### Resetting `robbytheadmin`

A command-line script restores the seeded admin's password to the default without affecting other accounts:

```bash
docker exec -it demo-hr-sot python -m scripts.reset_admin_password
```

This script:
- Only affects the `robbytheadmin` account (flagged `is_seeded=true`)
- Re-enables the account if it had been disabled
- Does not touch other admin accounts, API keys, or OAuth clients

### Rotating an API key

1. Create a new API key with a name indicating purpose
2. Update the consumer (e.g., Saviynt) to use the new key
3. Confirm the new key works (check `last_used_at` increments)
4. Revoke the old key from the UI

### Rotating an OAuth client secret

Same pattern as API keys — create new, update consumer, verify, revoke old.

### Rotating the JWT signing key

This invalidates all currently issued tokens. To do it:

```bash
docker exec -it demo-hr-sot rm /data/jwt_signing_key
docker restart demo-hr-sot
```

A new key is generated on the next startup. Any active JWT tokens will start returning 401 and clients will re-authenticate.

## Data protection

- All credentials (passwords, API keys, OAuth secrets, JWT signing key) are stored in the SQLite database or `/data` volume
- The `/data` volume must be protected at the filesystem level — anyone with read access to it can extract the JWT signing key and signing-key-derived tokens
- Database file is not encrypted at rest (POC tradeoff)
- For cloud deployments, use encrypted EBS / Azure Disk / PV — the platform handles encryption
- No PII filtering or masking — what you put in is what comes out

## Transport security

- The container itself speaks plain HTTP on port 8000
- HTTPS must be terminated by an upstream load balancer, reverse proxy, or ingress controller
- Cookie `Secure` flag is set if the app detects HTTPS via `X-Forwarded-Proto`
- `HttpOnly` and `SameSite=Lax` set on session cookies

## Audit and logging

- All authentication events are logged: login success, login failure, API key use, OAuth token issuance
- Logs include credential identifier (username, API key prefix, OAuth client ID) but **never** the secret value
- `last_used_at` and `last_used_ip` are tracked on API keys and OAuth clients
- Standard request logs include path, method, status, response time, auth method, and credential identifier
- Logs are written to stdout as JSON — collect with the platform's standard log forwarding

## What this app deliberately does not do

- No rate limiting (apply at load balancer if needed)
- No request signing or HMAC validation
- No IP allowlisting (apply at network/firewall layer)
- No SCIM 2.0 compliance
- No SAML SSO (OIDC single sign-on for the UI *is* supported — see Settings → Identity Providers)
- No webhook delivery (Saviynt polls; no push)
- No multi-factor authentication

## Recommended hardening before any "real" use

If you find yourself wanting to use this app for anything beyond POC, before doing so:

1. Front it with a reverse proxy (Nginx, Traefik, Cloudflare, AWS ALB) terminating TLS
2. Apply IP allowlisting at the network layer for the API
3. Change `robbytheadmin` password immediately and delete `/data/INITIAL_CREDENTIALS.txt`
4. Set a strong, unique `HRSOT_SESSION_SECRET` via environment variable
5. Set short OAuth token lifetimes (300-900 seconds)
6. Set API key `expires_at` values rather than letting them live indefinitely
7. Move from SQLite to Postgres
8. Enable database-level encryption at rest
9. Set up log aggregation and alert on auth failures
10. Honestly, just don't — this isn't what the app is for. Use a real HR system.
