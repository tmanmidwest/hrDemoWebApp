"""API-key permission scopes.

Scopes give API keys least-privilege access to the REST API. Each bearer
endpoint requires a specific scope (see `app.services.auth.require_scope`); a
key only passes if it was granted that scope — or the wildcard `admin` scope.

Scopes are stored on the key as a single space-separated string (same
convention as `AuthProvider.scopes`).
"""

from __future__ import annotations

from collections.abc import Iterable

# Wildcard — a key holding this passes every scope check.
ADMIN = "admin"

# Canonical scope catalog, grouped for display, in UI order.
SCOPES: list[dict[str, str]] = [
    {"value": "employees:read", "group": "Employees", "label": "Read employees",
     "description": "List and view employee records."},
    {"value": "employees:write", "group": "Employees", "label": "Manage employees",
     "description": "Create, update, archive, terminate, and reactivate employees."},
    {"value": "lookups:read", "group": "Lookups", "label": "Read lookups",
     "description": "List and view countries, states, statuses, departments, titles, locations."},
    {"value": "lookups:write", "group": "Lookups", "label": "Manage lookups",
     "description": "Create, update, and delete lookup records."},
    {"value": "users:read", "group": "Console users", "label": "Read users",
     "description": "List and view console accounts."},
    {"value": "users:write", "group": "Console users", "label": "Manage users",
     "description": "Create, update, and enable/disable console accounts."},
    {"value": "backup:create", "group": "System", "label": "Create backups",
     "description": "Generate and download a full-instance backup (includes secret keys)."},
    {"value": ADMIN, "group": "System", "label": "Full admin",
     "description": "Full access to every API-key-authorized endpoint."},
]

VALID_SCOPES: frozenset[str] = frozenset(s["value"] for s in SCOPES)

# Named presets that tick a sensible set of scopes.
PRESETS: dict[str, list[str]] = {
    "Employee Management": ["employees:read", "employees:write", "lookups:read"],
    "Read-Only (View All)": ["employees:read", "lookups:read", "users:read"],
    "Full Admin": [ADMIN],
}


def parse(raw: str | None) -> set[str]:
    """Parse a stored space-separated scope string into a set."""
    if not raw:
        return set()
    return {tok for tok in raw.split() if tok}


def serialize(scopes: Iterable[str]) -> str:
    """Serialize scopes to the stored space-separated form (deduped, ordered)."""
    unique = set(scopes)
    # Keep catalog order for stable, readable storage.
    return " ".join(s["value"] for s in SCOPES if s["value"] in unique)


def validate(scopes: Iterable[str]) -> list[str]:
    """Return only the recognized scopes, in catalog order (unknowns dropped)."""
    requested = set(scopes)
    return [s["value"] for s in SCOPES if s["value"] in requested]


def has_scope(granted: Iterable[str], required: str) -> bool:
    """True if `granted` satisfies `required` (the `admin` wildcard matches all)."""
    granted_set = set(granted)
    return ADMIN in granted_set or required in granted_set
