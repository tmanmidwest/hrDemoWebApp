"""Live IGA-governance guide for console users.

Where :mod:`app.services.schema_export` describes the *employee* data surface
(the system as a Source of Truth), this describes the **console-user lifecycle**
surface — everything an IGA platform (e.g. Saviynt) needs to govern the accounts
that sign in to this app: create, update, disable/enable an account, read the
role (entitlement) catalog, and change a user's role.

It is assembled from the live users/roles API so the reference always matches
what this instance actually serves. Two renderings:
* :func:`build_schema` — a structured dict (JSON export + UI page).
* :func:`schema_to_csv` — a flat account-attribute catalog for field mapping.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import __version__
from app.models import AppUser, UserRole

# ---------------------------------------------------------------------------
# Static descriptors — the user-governance API is a fixed surface (unlike the
# employee schema, there are no instance-specific custom fields).
# ---------------------------------------------------------------------------

# Scopes an IGA connector needs, and why.
_SCOPES: list[tuple[str, str]] = [
    ("users:read", "List and read console accounts (and their assigned role)."),
    ("users:write", "Create and update accounts, change a role, and disable/enable."),
    ("roles:read", "Read the role (entitlement) catalog."),
]

# The lifecycle operations, mapped to the IGA verbs a connector implements.
# (action, method, path, scope, success, body, description)
_OPERATIONS: list[tuple[str, str, str, str, int, dict[str, str] | None, str]] = [
    (
        "List accounts", "GET", "/api/v1/users", "users:read", 200, None,
        "Full account list for reconciliation. Returns the account attributes below.",
    ),
    (
        "Get account", "GET", "/api/v1/users/{id}", "users:read", 200, None,
        "Read a single account by its immutable id, including its current role.",
    ),
    (
        "Create account", "POST", "/api/v1/users", "users:write", 201,
        {"username": "string (required, unique)",
         "password": "string (required, min 8)",
         "role": "string (optional; defaults to view_only)"},
        "Provision a new local account. 409 if the username is already taken.",
    ),
    (
        "Update account", "PATCH", "/api/v1/users/{id}", "users:write", 200,
        {"username": "string (optional)",
         "password": "string (optional, min 8)",
         "role": "string (optional)"},
        "Update any subset of username, password, and/or role. Omitted fields are left unchanged.",
    ),
    (
        "Change role (entitlement)", "PATCH", "/api/v1/users/{id}", "users:write", 200,
        {"role": "string (one of the role ids below)"},
        "Grant/replace the account's role. Role is single-valued, so this replaces any prior role.",
    ),
    (
        "Disable account (deprovision)", "POST", "/api/v1/users/{id}/disable",
        "users:write", 200, None,
        "Soft-disable: the account can no longer sign in. Reversible via enable. This is how you deprovision — accounts are never hard-deleted through the API.",
    ),
    (
        "Enable account (reprovision)", "POST", "/api/v1/users/{id}/enable",
        "users:write", 200, None,
        "Re-activate a previously disabled account.",
    ),
    (
        "List roles (entitlement catalog)", "GET", "/api/v1/roles", "roles:read", 200, None,
        "The fixed set of assignable roles. Always available regardless of who is assigned; import as the entitlement list.",
    ),
]

# The account object attributes (mirrors UserOut / UserCreate / UserUpdate).
# (name, type, access, required_on_create, updatable, description)
_ACCOUNT_ATTRIBUTES: list[tuple[str, str, str, bool, bool, str]] = [
    ("id", "integer", "read-only", False, False,
     "Immutable numeric id — the stable correlation key across reconciliations."),
    ("username", "string", "read-write", True, True,
     "Login name. Unique across accounts; used as the human-facing correlation key."),
    ("password", "string", "write-only", True, True,
     "Plaintext on write only (min 8 chars); never returned. Omit for SSO-provisioned accounts, which have none."),
    ("role", "string (enum)", "read-write", False, True,
     "The account's single assigned role/entitlement — one of the role ids below. Defaults to view_only on create."),
    ("is_active", "boolean", "read-only", False, False,
     "Whether the account can sign in. Not set via PATCH — use the disable/enable operations."),
    ("is_seeded", "boolean", "read-only", False, False,
     "True for the bootstrap admin account, which cannot be disabled or have its role changed."),
    ("auth_type", "string (enum)", "read-only", False, False,
     "'local' (password) or 'sso' (provisioned via an identity provider)."),
    ("created_at", "datetime", "read-only", False, False, "Account creation timestamp."),
    ("last_login_at", "datetime | null", "read-only", False, False,
     "Last successful sign-in, or null if never signed in."),
]

# Governance rules a connector must respect.
_NOTES: list[str] = [
    "Deprovisioning is a disable (POST …/disable), not a delete — accounts are "
    "never hard-deleted through the API, so history and audit trails are preserved.",
    "The seeded admin account cannot be disabled or demoted, guaranteeing at least "
    "one active admin always remains.",
    "Roles are single-valued: an account holds exactly one role at a time, so "
    "changing the role replaces the previous one.",
    "Usernames are unique; creating or renaming to a taken username returns 409.",
    "All write operations require the users:write scope; reading the role catalog "
    "requires roles:read.",
    "Every lifecycle call is written to the activity log. Credential handling is "
    "auditable but privacy-preserving: create records a password_set flag and an "
    "update lists 'password' among its changed fields to show THAT a credential "
    "was provisioned — the password value itself is never logged.",
]


def build_schema(db: Session) -> dict[str, Any]:
    """Build the structured user-governance guide for the live instance."""
    total = db.query(AppUser).count()
    active = db.query(AppUser).filter(AppUser.is_active).count()

    return {
        "instance": {
            "app_version": __version__,
            "account_count": total,
            "active_account_count": active,
            "note": (
                "Describes the console-user (login account) governance surface for "
                "this instance — distinct from the employee data surface at "
                "/ui/admin/schema."
            ),
        },
        "auth": {
            "base_path": "/api/v1",
            "methods": [
                "Bearer API key (Authorization: Bearer <key>)",
                "OAuth2 client-credentials (Authorization: Bearer <access_token>)",
            ],
            "recommended_key_preset": "IGA / Provisioning",
            "scopes": [{"scope": s, "purpose": why} for s, why in _SCOPES],
        },
        "correlation": {
            "immutable_id": "id",
            "natural_key": "username",
            "note": (
                "Correlate on id where possible (immutable); username is the "
                "human-facing natural key and is unique but can be renamed."
            ),
        },
        "operations": [
            {
                "action": action,
                "method": method,
                "path": path,
                "scope": scope,
                "success_status": success,
                "request_body": body,
                "description": description,
            }
            for action, method, path, scope, success, body, description in _OPERATIONS
        ],
        "account_attributes": [
            {
                "name": name,
                "type": type_,
                "access": access,
                "required_on_create": required,
                "updatable": updatable,
                "description": description,
            }
            for name, type_, access, required, updatable, description in _ACCOUNT_ATTRIBUTES
        ],
        "entitlements": {
            "model": "single-valued role attribute on the account",
            "catalog_endpoint": "GET /api/v1/roles",
            "assign_via": "PATCH /api/v1/users/{id} with {\"role\": <role id>}",
            "roles": UserRole.catalog(),
        },
        "notes": _NOTES,
    }


def schema_to_csv(schema: dict[str, Any]) -> str:
    """Flatten the account-attribute catalog to CSV for field mapping."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "name", "type", "access", "required_on_create", "updatable", "description"
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    for attr in schema.get("account_attributes", []):
        row = dict(attr)
        row["required_on_create"] = "yes" if attr.get("required_on_create") else "no"
        row["updatable"] = "yes" if attr.get("updatable") else "no"
        writer.writerow(row)
    return buffer.getvalue()
