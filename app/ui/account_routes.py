"""Self-service account management for the signed-in user.

Available to every authenticated user regardless of role (``require_ui_user``),
unlike the admin-only reset flow under Settings → Users which targets *other*
accounts. Currently a single action: change your own password.

Changing your password requires re-entering the current one — a signed-in
session alone is not enough — and the plaintext is never logged. Accounts
provisioned via SSO have no local password and are directed to their identity
provider instead.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser
from app.services.audit import record_event
from app.services.passwords import hash_password, verify_password
from app.ui.dependencies import require_ui_user
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/account", tags=["ui"], include_in_schema=False)

_MIN_PASSWORD_LEN = 8


def _is_sso_only(user: AppUser) -> bool:
    """True for accounts with no local password (provisioned via an IdP)."""
    return not user.password_hash


@router.get("/password")
def show_password_form(
    request: Request,
    user: AppUser = Depends(require_ui_user),
) -> Response:
    """Render the self-service change-password form for the current user."""
    return render(
        request,
        "account/password.html",
        current_user=user,
        sso_only=_is_sso_only(user),
    )


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    """Change the current user's own password after verifying the current one."""

    def _error(message: str) -> Response:
        return render(
            request,
            "account/password.html",
            current_user=user,
            sso_only=_is_sso_only(user),
            error=message,
        )

    # SSO accounts have no local password to change.
    if _is_sso_only(user):
        return _error(
            "Your account signs in through an identity provider, so there is no "
            "local password to change here."
        )

    if not verify_password(current_password, user.password_hash or ""):
        log.info("ui_self_password_change_denied", extra={"user_id": user.id})
        return _error("Your current password is incorrect.")

    if new_password != confirm_password:
        return _error("The new passwords do not match.")
    if len(new_password) < _MIN_PASSWORD_LEN:
        return _error(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
    if new_password == current_password:
        return _error("Choose a new password that is different from the current one.")

    user.password_hash = hash_password(new_password)
    db.commit()

    log.info("ui_self_password_changed", extra={"user_id": user.id})
    record_event(
        category="app_user",
        event_type="app_user.password_changed",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type="app_user",
        target_id=user.id,
        target_label=user.username,
        message=f"Changed own password ('{user.username}')",
        # Records only THAT the password changed — never the value.
        detail={"surface": "ui", "self_service": True},
        request=request,
    )
    flash(request, "Your password has been updated.", "success")
    return RedirectResponse(url="/ui/employees", status_code=303)
