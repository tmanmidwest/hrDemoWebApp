"""OIDC single sign-on endpoints for the web UI.

These sit alongside the local username/password login in auth_routes.py. The
flow is the standard OIDC Authorization Code grant (with PKCE):

    GET  /ui/auth/{slug}/login     -> redirect to the identity provider
    GET  /ui/auth/{slug}/callback  -> exchange code, provision user, set session
"""

from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuthProvider
from app.services.auth import SESSION_USER_ID_KEY, SESSION_USERNAME_KEY
from app.services.oidc import build_client, callback_url, find_or_create_user
from app.ui.flash import flash

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/auth", tags=["ui"], include_in_schema=False)

_NEXT_SESSION_KEY = "_oidc_next"


def _enabled_provider(db: Session, slug: str) -> AuthProvider | None:
    return (
        db.query(AuthProvider)
        .filter(AuthProvider.slug == slug, AuthProvider.is_enabled.is_(True))
        .one_or_none()
    )


@router.get("/{slug}/login")
async def oidc_login(
    slug: str,
    request: Request,
    next: str = "/ui/employees",
    db: Session = Depends(get_db),
) -> Response:
    """Kick off the OIDC flow: redirect the browser to the identity provider."""
    provider = _enabled_provider(db, slug)
    if provider is None:
        flash(request, "That sign-in option is not available.", "error")
        return RedirectResponse(url="/ui/login", status_code=303)

    request.session[_NEXT_SESSION_KEY] = next
    client = build_client(provider)
    redirect_uri = callback_url(request, slug)
    try:
        return await client.authorize_redirect(request, redirect_uri)
    except OAuthError as exc:
        log.warning("oidc_authorize_redirect_failed", extra={"provider": slug, "error": str(exc)})
        flash(request, f"Could not start sign-in with {provider.display_name}.", "error")
        return RedirectResponse(url="/ui/login", status_code=303)


@router.get("/{slug}/callback")
async def oidc_callback(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Handle the provider redirect: exchange the code and log the user in."""
    provider = _enabled_provider(db, slug)
    if provider is None:
        flash(request, "That sign-in option is not available.", "error")
        return RedirectResponse(url="/ui/login", status_code=303)

    client = build_client(provider)
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("oidc_callback_failed", extra={"provider": slug, "error": str(exc)})
        flash(request, f"Sign-in with {provider.display_name} failed or was cancelled.", "error")
        return RedirectResponse(url="/ui/login", status_code=303)

    claims = token.get("userinfo")
    if not claims:
        claims = await client.userinfo(token=token)
    if not claims or not claims.get("sub"):
        log.warning("oidc_no_subject", extra={"provider": slug})
        flash(request, "The identity provider did not return a usable profile.", "error")
        return RedirectResponse(url="/ui/login", status_code=303)

    user = find_or_create_user(db, provider, dict(claims))
    if not user.is_active:
        flash(request, "Your account is disabled. Contact an administrator.", "error")
        return RedirectResponse(url="/ui/login", status_code=303)

    request.session[SESSION_USER_ID_KEY] = user.id
    request.session[SESSION_USERNAME_KEY] = user.username
    next_url = request.session.pop(_NEXT_SESSION_KEY, "/ui/employees")

    log.info(
        "oidc_login_success",
        extra={"username": user.username, "user_id": user.id, "provider": slug},
    )
    flash(request, f"Signed in as {user.username} via {provider.display_name}.", "success")
    return RedirectResponse(url=next_url, status_code=303)
