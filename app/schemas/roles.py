"""Pydantic schema for the role (entitlement) catalog API.

Roles are the fixed, enum-backed set of console-account authorization levels
(View Only / Management / Admin). The catalog is read-only and always present
regardless of who is assigned; a user's assigned role is read and written
through the users API (see app/schemas/users.py). An IGA platform imports this
catalog as its entitlement list and correlates on `id`.
"""

from __future__ import annotations

from pydantic import BaseModel


class RoleOut(BaseModel):
    """A role as returned by the catalog API.

    `id` is the stored role value (e.g. ``"admin"``) — the same value that
    appears as `role` on a user, so an IGA connector can map a user's role
    directly onto the imported entitlement.
    """

    id: str
    name: str
    description: str
