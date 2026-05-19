"""UI-specific auth dependency.

The JSON `get_current_user` raises 401 — fine for JSON endpoints, awful for
HTML routes. This one raises a special exception that the app catches and
turns into a redirect to /ui/login?next=<original-url>.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import get_db
from app.models import AppUser
from app.services.auth import SESSION_USER_ID_KEY


class _RedirectToLogin(StarletteHTTPException):
    """Sentinel exception that an exception handler turns into a redirect."""

    def __init__(self, next_url: str) -> None:
        super().__init__(status_code=302, detail="login required")
        self.next_url = next_url


def require_ui_user(
    request: Request, db: Session = Depends(get_db)
) -> AppUser:
    """Return the logged-in AppUser, or redirect to /ui/login on failure."""
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if not user_id:
        raise _RedirectToLogin(next_url=str(request.url.path))
    user = db.get(AppUser, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise _RedirectToLogin(next_url=str(request.url.path))
    return user


def redirect_to_login_handler(_request: Request, exc: _RedirectToLogin) -> RedirectResponse:
    next_param = quote(exc.next_url, safe="/")
    return RedirectResponse(url=f"/ui/login?next={next_param}", status_code=303)


# Re-export for main.py
RedirectToLogin = _RedirectToLogin
