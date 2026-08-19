"""Role (entitlement) catalog endpoint.

Roles are the fixed console-account authorization levels — View Only /
Management / Admin — backed by the `UserRole` enum. This catalog is read-only
and always available regardless of whether any account currently holds a role,
so an IGA platform (e.g. Saviynt) can import it as its entitlement list.

A user's assigned role is read via ``GET /api/v1/users/{id}`` and set via
``PATCH /api/v1/users/{id}`` (the ``role`` field) — there is no separate
assignment resource, because each account holds exactly one role.

Listing requires a bearer token (API key or OAuth client) carrying the
``roles:read`` scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models import UserRole
from app.schemas.roles import RoleOut
from app.services.auth import Principal, require_scope

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("/", response_model=list[RoleOut])
def list_roles(
    _principal: Principal = Depends(require_scope("roles:read")),
) -> list[dict[str, str]]:
    """List the fixed role catalog (the entitlements an IGA imports)."""
    return UserRole.catalog()
