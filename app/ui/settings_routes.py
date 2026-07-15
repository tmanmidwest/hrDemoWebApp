"""UI routes for settings: admin users, API keys, OAuth clients, SSO, reset."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    ApiKey,
    AppBranding,
    AppUser,
    AuthProvider,
    OAuthClient,
    UserIdentity,
    UserRole,
)
from app.models.app_branding import BRANDING_ID
from app.models.audit_event import AuditEvent
from app.models.auth_provider import DEFAULT_SCOPES
from app.services import backup as backup_service
from app.services import branding as branding_service
from app.services import mcp_gateway_tokens, mcp_token, seed_data, system_config
from app.services import scopes as scope_service
from app.services.audit import prune_old_events, record_event
from app.services.oidc import callback_url
from app.services.passwords import hash_password
from app.services.secret_box import encrypt_secret
from app.services.tokens import (
    generate_api_key,
    generate_oauth_client_id,
    generate_oauth_client_secret,
    hash_token,
)
from app.ui.dependencies import require_admin
from app.ui.flash import flash
from app.ui.templating import render

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/settings", tags=["ui"], include_in_schema=False)

# Roles offered in the create/edit-user forms, in display order.
ROLE_CHOICES = [
    (UserRole.ADMIN.value, "Admin"),
    (UserRole.MANAGEMENT.value, "Management"),
    (UserRole.VIEW_ONLY.value, "View Only"),
]
_VALID_ROLES = {value for value, _ in ROLE_CHOICES}


def _settings_event(
    request: Request,
    user: AppUser,
    *,
    category: str,
    event_type: str,
    target_type: str | None = None,
    target_id: object = None,
    target_label: str | None = None,
    message: str = "",
    detail: dict | None = None,
) -> None:
    """Record a settings-area audit event performed by a logged-in admin."""
    record_event(
        category=category,
        event_type=event_type,
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        message=message,
        detail={"surface": "ui", **(detail or {})},
        request=request,
    )


# ===========================================================================
# Settings index
# ===========================================================================


@router.get("")
@router.get("/")
def settings_index(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    """Landing page linking to each settings area (admin only)."""
    return render(
        request,
        "settings/index.html",
        current_user=user,
        active_section="settings",
    )


# ===========================================================================
# Users
# ===========================================================================


@router.get("/admin-users")
def list_admins(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    users = db.query(AppUser).order_by(AppUser.username).all()
    return render(
        request,
        "settings/admin_users.html",
        current_user=user,
        active_subsection="admin_users",
        users=users,
        role_choices=ROLE_CHOICES,
    )


@router.get("/admin-users/new")
def show_new_admin(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "settings/admin_user_new.html",
        current_user=user,
        active_subsection="admin_users",
        form={"role": UserRole.VIEW_ONLY.value},
        role_choices=ROLE_CHOICES,
    )


@router.post("/admin-users/new")
def create_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(UserRole.VIEW_ONLY.value),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    username = username.strip()
    # Fall back to the least-privileged role for any unrecognized value.
    if role not in _VALID_ROLES:
        role = UserRole.VIEW_ONLY.value
    if len(password) < 8:
        return render(
            request,
            "settings/admin_user_new.html",
            current_user=user,
            active_subsection="admin_users",
            form={"username": username, "role": role},
            role_choices=ROLE_CHOICES,
            error="Password must be at least 8 characters.",
        )

    new_user = AppUser(
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        is_seeded=False,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "settings/admin_user_new.html",
            current_user=user,
            active_subsection="admin_users",
            form={"username": username, "role": role},
            role_choices=ROLE_CHOICES,
            error=f"Username '{username}' is already taken.",
        )
    log.info(
        "ui_admin_created",
        extra={
            "target_user_id": new_user.id,
            "target_username": username,
            "role": role,
            "by": user.username,
        },
    )
    _settings_event(
        request, user,
        category="admin_user",
        event_type="admin_user.created",
        target_type="app_user",
        target_id=new_user.id,
        target_label=username,
        message=f"Created user '{username}' ({new_user.role_label})",
        detail={"role": role},
    )
    flash(request, f"User '{username}' created ({new_user.role_label}).", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.post("/admin-users/{user_id}/role")
def change_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if role not in _VALID_ROLES:
        flash(request, "Unknown role.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)
    # Guard against locking yourself (or the bootstrap admin) out of admin access.
    if target.is_seeded:
        flash(request, "The seeded admin's role cannot be changed.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)
    if target.id == user.id:
        flash(request, "You cannot change your own role.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)

    if target.role == role:
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)

    previous = target.role
    target.role = role
    db.commit()
    log.info(
        "ui_user_role_changed",
        extra={
            "target_user_id": target.id,
            "target_username": target.username,
            "from": previous,
            "to": role,
            "by": user.username,
        },
    )
    _settings_event(
        request, user,
        category="admin_user",
        event_type="admin_user.role_changed",
        target_type="app_user",
        target_id=target.id,
        target_label=target.username,
        message=f"Changed role of '{target.username}' to {target.role_label}",
        detail={"from": previous, "to": role},
    )
    flash(request, f"Role updated for {target.username} ({target.role_label}).", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.get("/admin-users/{user_id}/password")
def show_password_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Admin user not found.")
    return render(
        request,
        "settings/admin_user_password.html",
        current_user=user,
        active_subsection="admin_users",
        target_user=target,
    )


@router.post("/admin-users/{user_id}/password")
def change_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Admin user not found.")

    if new_password != confirm_password:
        return render(
            request,
            "settings/admin_user_password.html",
            current_user=user,
            active_subsection="admin_users",
            target_user=target,
            error="Passwords do not match.",
        )
    if len(new_password) < 8:
        return render(
            request,
            "settings/admin_user_password.html",
            current_user=user,
            active_subsection="admin_users",
            target_user=target,
            error="Password must be at least 8 characters.",
        )

    target.password_hash = hash_password(new_password)
    db.commit()
    log.info(
        "ui_password_changed",
        extra={"target_user_id": target.id, "target_username": target.username, "by": user.username},
    )
    _settings_event(
        request, user,
        category="admin_user",
        event_type="admin_user.password_changed",
        target_type="app_user",
        target_id=target.id,
        target_label=target.username,
        message=f"Changed password for admin user '{target.username}'",
    )
    flash(request, f"Password updated for {target.username}.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.post("/admin-users/{user_id}/delete")
def delete_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Admin user not found.")
    if target.is_seeded:
        flash(request, "Cannot delete the seeded admin user. Disable it instead.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)
    if target.id == user.id:
        flash(request, "You cannot delete your own account.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)
    db.delete(target)
    db.commit()
    log.info(
        "ui_admin_deleted",
        extra={"target_user_id": user_id, "target_username": target.username, "by": user.username},
    )
    _settings_event(
        request, user,
        category="admin_user",
        event_type="admin_user.deleted",
        target_type="app_user",
        target_id=user_id,
        target_label=target.username,
        message=f"Deleted admin user '{target.username}'",
    )
    flash(request, f"Deleted admin user '{target.username}'.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.post("/admin-users/{user_id}/disable")
def disable_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.is_seeded:
        flash(request, "The seeded admin cannot be disabled.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)
    if target.id == user.id:
        flash(request, "You cannot disable your own account.", "error")
        return RedirectResponse(url="/ui/settings/admin-users", status_code=303)
    if target.is_active:
        target.is_active = False
        db.commit()
        log.info(
            "ui_user_disabled",
            extra={"target_user_id": target.id, "target_username": target.username, "by": user.username},
        )
        _settings_event(
            request, user,
            category="admin_user",
            event_type="admin_user.disabled",
            target_type="app_user",
            target_id=target.id,
            target_label=target.username,
            message=f"Disabled user '{target.username}'",
        )
        flash(request, f"Disabled user '{target.username}'.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.post("/admin-users/{user_id}/enable")
def enable_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if not target.is_active:
        target.is_active = True
        db.commit()
        log.info(
            "ui_user_enabled",
            extra={"target_user_id": target.id, "target_username": target.username, "by": user.username},
        )
        _settings_event(
            request, user,
            category="admin_user",
            event_type="admin_user.enabled",
            target_type="app_user",
            target_id=target.id,
            target_label=target.username,
            message=f"Enabled user '{target.username}'",
        )
        flash(request, f"Enabled user '{target.username}'.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


# ===========================================================================
# API Keys
# ===========================================================================


@router.get("/api-keys")
def list_api_keys(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
    # Pull new_key out of session (one-shot reveal after creation)
    new_key = request.session.pop("_revealed_api_key", None)
    return render(
        request,
        "settings/api_keys.html",
        current_user=user,
        active_subsection="api_keys",
        keys=keys,
        new_key=new_key,
        # The MCP server's own key is managed on the MCP page; flag+protect it here.
        mcp_key_id=mcp_token.current_key_id(db),
    )


@router.get("/api-keys/new")
def show_new_api_key(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "settings/api_key_new.html",
        current_user=user,
        active_subsection="api_keys",
        scope_catalog=scope_service.SCOPES,
        scope_presets=scope_service.PRESETS,
        form={"scopes": scope_service.PRESETS["Employee Management"]},
    )


@router.post("/api-keys/new")
def create_api_key(
    request: Request,
    name: str = Form(...),
    scopes: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    granted = scope_service.validate(scopes)
    if not granted:
        return render(
            request,
            "settings/api_key_new.html",
            current_user=user,
            active_subsection="api_keys",
            scope_catalog=scope_service.SCOPES,
            scope_presets=scope_service.PRESETS,
            form={"name": name.strip(), "scopes": []},
            error="Select at least one permission for this key.",
        )

    full_key, prefix = generate_api_key()
    key = ApiKey(
        name=name.strip(),
        key_prefix=prefix,
        key_hash=hash_token(full_key),
        created_by_user_id=user.id,
        scopes=scope_service.serialize(granted),
    )
    db.add(key)
    db.commit()
    log.info(
        "ui_api_key_created",
        extra={"api_key_id": key.id, "key_name": key.name, "prefix": prefix,
               "scopes": key.scopes, "by": user.username},
    )
    _settings_event(
        request, user,
        category="api_key",
        event_type="api_key.created",
        target_type="api_key",
        target_id=key.id,
        target_label=key.name,
        message=f"Created API key '{key.name}'",
        detail={"prefix": prefix, "scopes": granted},
    )
    # Stash the full key in session so the list page can reveal it once
    request.session["_revealed_api_key"] = full_key
    return RedirectResponse(url="/ui/settings/api-keys", status_code=303)


@router.post("/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    if key_id == mcp_token.current_key_id(db):
        flash(
            request,
            "That's the MCP server's key — manage it on the MCP page.",
            "warning",
        )
        return RedirectResponse(url="/ui/settings/mcp", status_code=303)
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        db.commit()
        log.info("ui_api_key_revoked", extra={"api_key_id": key_id, "by": user.username})
        _settings_event(
            request, user,
            category="api_key",
            event_type="api_key.revoked",
            target_type="api_key",
            target_id=key.id,
            target_label=key.name,
            message=f"Revoked API key '{key.name}'",
        )
        flash(request, f"Revoked API key '{key.name}'.", "success")
    return RedirectResponse(url="/ui/settings/api-keys", status_code=303)


@router.post("/api-keys/{key_id}/delete")
def delete_api_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    if key_id == mcp_token.current_key_id(db):
        flash(
            request,
            "That's the MCP server's key — manage it on the MCP page.",
            "warning",
        )
        return RedirectResponse(url="/ui/settings/mcp", status_code=303)
    name = key.name
    db.delete(key)
    db.commit()
    log.info("ui_api_key_deleted", extra={"api_key_id": key_id, "by": user.username})
    _settings_event(
        request, user,
        category="api_key",
        event_type="api_key.deleted",
        target_type="api_key",
        target_id=key_id,
        target_label=name,
        message=f"Deleted API key '{name}'",
    )
    flash(request, f"Deleted API key '{name}'.", "success")
    return RedirectResponse(url="/ui/settings/api-keys", status_code=303)


# ===========================================================================
# MCP server — outbound API token + inbound gateway tokens
# ===========================================================================


@router.get("/mcp")
def show_mcp_token(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    """Settings → MCP: manage the MCP server's two credentials."""
    return render(
        request,
        "settings/mcp.html",
        current_user=user,
        active_subsection="mcp",
        status=mcp_token.status(db),
        new_token=request.session.pop("_revealed_mcp_token", None),
        gateway_tokens=mcp_gateway_tokens.list_tokens(db),
        new_gateway_token=request.session.pop("_revealed_gateway_token", None),
    )


# --- Outbound service token (MCP server → this app) ---


@router.post("/mcp/rotate")
def rotate_mcp_token(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    full_key = mcp_token.rotate(db, actor_id=user.id)
    _settings_event(
        request, user,
        category="mcp",
        event_type="mcp.token.rotated",
        target_type="mcp_token",
        target_label=mcp_token.MCP_KEY_NAME,
        message="Rotated the MCP server API token",
    )
    request.session["_revealed_mcp_token"] = full_key
    flash(request, "Generated a new MCP API token.", "success")
    return RedirectResponse(url="/ui/settings/mcp", status_code=303)


@router.post("/mcp/clear")
def clear_mcp_token(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    existed = mcp_token.clear(db)
    if existed:
        _settings_event(
            request, user,
            category="mcp",
            event_type="mcp.token.cleared",
            target_type="mcp_token",
            target_label=mcp_token.MCP_KEY_NAME,
            message="Cleared and revoked the MCP server API token",
        )
        flash(request, "Cleared and revoked the MCP API token.", "success")
    return RedirectResponse(url="/ui/settings/mcp", status_code=303)


# --- Inbound gateway tokens (clients → MCP server) ---


@router.post("/mcp/gateway/tokens/new")
def create_gateway_token(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    label = name.strip()
    if not label:
        flash(request, "Give the gateway token a name.", "error")
        return RedirectResponse(url="/ui/settings/mcp", status_code=303)
    row, full = mcp_gateway_tokens.create(db, name=label, actor_id=user.id)
    _settings_event(
        request, user,
        category="mcp",
        event_type="mcp.gateway_token.created",
        target_type="mcp_gateway_token",
        target_id=row.id,
        target_label=row.name,
        message=f"Created MCP gateway token '{row.name}'",
        detail={"prefix": row.token_prefix},
    )
    request.session["_revealed_gateway_token"] = full
    flash(request, f"Created gateway token '{row.name}'.", "success")
    return RedirectResponse(url="/ui/settings/mcp", status_code=303)


@router.post("/mcp/gateway/tokens/{token_id}/revoke")
def revoke_gateway_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    row = mcp_gateway_tokens.revoke(db, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Gateway token not found.")
    _settings_event(
        request, user,
        category="mcp",
        event_type="mcp.gateway_token.revoked",
        target_type="mcp_gateway_token",
        target_id=row.id,
        target_label=row.name,
        message=f"Revoked MCP gateway token '{row.name}'",
    )
    flash(request, f"Revoked gateway token '{row.name}'.", "success")
    return RedirectResponse(url="/ui/settings/mcp", status_code=303)


@router.post("/mcp/gateway/tokens/{token_id}/delete")
def delete_gateway_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    name = mcp_gateway_tokens.delete(db, token_id)
    if name is None:
        raise HTTPException(status_code=404, detail="Gateway token not found.")
    _settings_event(
        request, user,
        category="mcp",
        event_type="mcp.gateway_token.deleted",
        target_type="mcp_gateway_token",
        target_id=token_id,
        target_label=name,
        message=f"Deleted MCP gateway token '{name}'",
    )
    flash(request, f"Deleted gateway token '{name}'.", "success")
    return RedirectResponse(url="/ui/settings/mcp", status_code=303)


# ===========================================================================
# OAuth Clients
# ===========================================================================


@router.get("/oauth-clients")
def list_oauth_clients(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    clients = db.query(OAuthClient).order_by(OAuthClient.created_at.desc()).all()
    new_client = request.session.pop("_revealed_oauth_client", None)
    return render(
        request,
        "settings/oauth_clients.html",
        current_user=user,
        active_subsection="oauth_clients",
        clients=clients,
        new_client=new_client,
    )


@router.get("/oauth-clients/new")
def show_new_oauth_client(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "settings/oauth_client_new.html",
        current_user=user,
        active_subsection="oauth_clients",
    )


@router.post("/oauth-clients/new")
def create_oauth_client(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    client_id = generate_oauth_client_id()
    client_secret = generate_oauth_client_secret()
    client = OAuthClient(
        name=name.strip(),
        client_id=client_id,
        client_secret_hash=hash_token(client_secret),
        created_by_user_id=user.id,
    )
    db.add(client)
    db.commit()
    log.info(
        "ui_oauth_client_created",
        extra={"oauth_client_id": client.id, "client_id": client_id, "by": user.username},
    )
    _settings_event(
        request, user,
        category="oauth_client",
        event_type="oauth_client.created",
        target_type="oauth_client",
        target_id=client.id,
        target_label=client.name,
        message=f"Created OAuth client '{client.name}'",
        detail={"client_id": client_id},
    )
    request.session["_revealed_oauth_client"] = {
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return RedirectResponse(url="/ui/settings/oauth-clients", status_code=303)


@router.post("/oauth-clients/{client_pk}/revoke")
def revoke_oauth_client(
    client_pk: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    client = db.get(OAuthClient, client_pk)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth client not found.")
    if client.revoked_at is None:
        client.revoked_at = datetime.now(UTC)
        db.commit()
        log.info("ui_oauth_client_revoked", extra={"oauth_client_id": client_pk, "by": user.username})
        _settings_event(
            request, user,
            category="oauth_client",
            event_type="oauth_client.revoked",
            target_type="oauth_client",
            target_id=client.id,
            target_label=client.name,
            message=f"Revoked OAuth client '{client.name}'",
        )
        flash(request, f"Revoked OAuth client '{client.name}'.", "success")
    return RedirectResponse(url="/ui/settings/oauth-clients", status_code=303)


@router.post("/oauth-clients/{client_pk}/delete")
def delete_oauth_client(
    client_pk: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    client = db.get(OAuthClient, client_pk)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth client not found.")
    name = client.name
    db.delete(client)
    db.commit()
    log.info("ui_oauth_client_deleted", extra={"oauth_client_id": client_pk, "by": user.username})
    _settings_event(
        request, user,
        category="oauth_client",
        event_type="oauth_client.deleted",
        target_type="oauth_client",
        target_id=client_pk,
        target_label=name,
        message=f"Deleted OAuth client '{name}'",
    )
    flash(request, f"Deleted OAuth client '{name}'.", "success")
    return RedirectResponse(url="/ui/settings/oauth-clients", status_code=303)


# ===========================================================================
# Identity Providers (OIDC single sign-on)
# ===========================================================================


@router.get("/auth-providers")
def list_auth_providers(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    providers = db.query(AuthProvider).order_by(AuthProvider.created_at.desc()).all()
    # The redirect/callback URI each provider must have registered at the IdP.
    redirect_uris = {p.id: callback_url(request, p.slug) for p in providers}
    return render(
        request,
        "settings/auth_providers.html",
        current_user=user,
        active_subsection="auth_providers",
        providers=providers,
        redirect_uris=redirect_uris,
    )


@router.get("/auth-providers/new")
def show_new_auth_provider(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "settings/auth_provider_form.html",
        current_user=user,
        active_subsection="auth_providers",
        provider=None,
        form={"scopes": DEFAULT_SCOPES, "is_enabled": True},
        # Show the callback pattern so the user can register it at the IdP.
        callback_base=callback_url(request, "SLUG").replace("/SLUG/", "/<slug>/"),
    )


def _validate_provider_form(
    slug: str, display_name: str, issuer_url: str, client_id: str
) -> str | None:
    """Return an error message if the form is invalid, else None."""
    if not _SLUG_RE.match(slug):
        return "Slug must contain only lowercase letters, numbers, and hyphens."
    if not display_name:
        return "Display name is required."
    if not issuer_url.startswith(("http://", "https://")):
        return "Issuer URL must start with http:// or https://."
    if not client_id:
        return "Client ID is required."
    return None


@router.post("/auth-providers/new")
def create_auth_provider(
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    issuer_url: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(""),
    scopes: str = Form(DEFAULT_SCOPES),
    is_enabled: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    slug = slug.strip().lower()
    display_name = display_name.strip()
    issuer_url = issuer_url.strip()
    client_id = client_id.strip()
    scopes = scopes.strip() or DEFAULT_SCOPES

    form = {
        "display_name": display_name,
        "slug": slug,
        "issuer_url": issuer_url,
        "client_id": client_id,
        "scopes": scopes,
        "is_enabled": bool(is_enabled),
    }

    error = _validate_provider_form(slug, display_name, issuer_url, client_id)
    if error:
        return render(
            request,
            "settings/auth_provider_form.html",
            current_user=user,
            active_subsection="auth_providers",
            provider=None,
            form=form,
            error=error,
            callback_base=callback_url(request, "SLUG").replace("/SLUG/", "/<slug>/"),
        )

    provider = AuthProvider(
        slug=slug,
        display_name=display_name,
        issuer_url=issuer_url,
        client_id=client_id,
        client_secret_encrypted=encrypt_secret(client_secret) if client_secret else "",
        scopes=scopes,
        is_enabled=bool(is_enabled),
        created_by_user_id=user.id,
    )
    db.add(provider)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "settings/auth_provider_form.html",
            current_user=user,
            active_subsection="auth_providers",
            provider=None,
            form=form,
            error=f"A provider with slug '{slug}' already exists.",
            callback_base=callback_url(request, "SLUG").replace("/SLUG/", "/<slug>/"),
        )
    log.info(
        "ui_auth_provider_created",
        extra={"provider_id": provider.id, "slug": slug, "by": user.username},
    )
    _settings_event(
        request, user,
        category="auth_provider",
        event_type="auth_provider.created",
        target_type="auth_provider",
        target_id=provider.id,
        target_label=display_name,
        message=f"Created identity provider '{display_name}'",
        detail={"slug": slug, "issuer_url": issuer_url},
    )
    flash(request, f"Identity provider '{display_name}' created.", "success")
    return RedirectResponse(url="/ui/settings/auth-providers", status_code=303)


@router.get("/auth-providers/{provider_id}/edit")
def show_edit_auth_provider(
    provider_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    provider = db.get(AuthProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Identity provider not found.")
    form = {
        "display_name": provider.display_name,
        "slug": provider.slug,
        "issuer_url": provider.issuer_url,
        "client_id": provider.client_id,
        "scopes": provider.scopes,
        "is_enabled": provider.is_enabled,
    }
    return render(
        request,
        "settings/auth_provider_form.html",
        current_user=user,
        active_subsection="auth_providers",
        provider=provider,
        form=form,
        redirect_uri=callback_url(request, provider.slug),
    )


@router.post("/auth-providers/{provider_id}/edit")
def update_auth_provider(
    provider_id: int,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    issuer_url: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(""),
    scopes: str = Form(DEFAULT_SCOPES),
    is_enabled: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    provider = db.get(AuthProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Identity provider not found.")

    slug = slug.strip().lower()
    display_name = display_name.strip()
    issuer_url = issuer_url.strip()
    client_id = client_id.strip()
    scopes = scopes.strip() or DEFAULT_SCOPES

    form = {
        "display_name": display_name,
        "slug": slug,
        "issuer_url": issuer_url,
        "client_id": client_id,
        "scopes": scopes,
        "is_enabled": bool(is_enabled),
    }

    error = _validate_provider_form(slug, display_name, issuer_url, client_id)
    if error:
        return render(
            request,
            "settings/auth_provider_form.html",
            current_user=user,
            active_subsection="auth_providers",
            provider=provider,
            form=form,
            error=error,
            redirect_uri=callback_url(request, provider.slug),
        )

    provider.slug = slug
    provider.display_name = display_name
    provider.issuer_url = issuer_url
    provider.client_id = client_id
    provider.scopes = scopes
    provider.is_enabled = bool(is_enabled)
    # Only replace the stored secret when a new one is supplied.
    if client_secret:
        provider.client_secret_encrypted = encrypt_secret(client_secret)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "settings/auth_provider_form.html",
            current_user=user,
            active_subsection="auth_providers",
            provider=provider,
            form=form,
            error=f"A provider with slug '{slug}' already exists.",
            redirect_uri=callback_url(request, provider.slug),
        )
    log.info(
        "ui_auth_provider_updated",
        extra={"provider_id": provider.id, "slug": slug, "by": user.username},
    )
    _settings_event(
        request, user,
        category="auth_provider",
        event_type="auth_provider.updated",
        target_type="auth_provider",
        target_id=provider.id,
        target_label=display_name,
        message=f"Updated identity provider '{display_name}'",
        detail={"slug": slug, "secret_rotated": bool(client_secret)},
    )
    flash(request, f"Identity provider '{display_name}' updated.", "success")
    return RedirectResponse(url="/ui/settings/auth-providers", status_code=303)


@router.post("/auth-providers/{provider_id}/toggle")
def toggle_auth_provider(
    provider_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    provider = db.get(AuthProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Identity provider not found.")
    provider.is_enabled = not provider.is_enabled
    db.commit()
    state = "enabled" if provider.is_enabled else "disabled"
    log.info(
        "ui_auth_provider_toggled",
        extra={"provider_id": provider_id, "state": state, "by": user.username},
    )
    _settings_event(
        request, user,
        category="auth_provider",
        event_type="auth_provider.toggled",
        target_type="auth_provider",
        target_id=provider.id,
        target_label=provider.display_name,
        message=f"{state.capitalize()} identity provider '{provider.display_name}'",
        detail={"state": state},
    )
    flash(request, f"Identity provider '{provider.display_name}' {state}.", "success")
    return RedirectResponse(url="/ui/settings/auth-providers", status_code=303)


@router.post("/auth-providers/{provider_id}/delete")
def delete_auth_provider(
    provider_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    provider = db.get(AuthProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Identity provider not found.")
    name = provider.display_name
    # Remove identity links explicitly — SQLite FK cascade isn't reliably on.
    db.query(UserIdentity).filter(UserIdentity.provider_id == provider.id).delete()
    db.delete(provider)
    db.commit()
    log.info(
        "ui_auth_provider_deleted",
        extra={"provider_id": provider_id, "by": user.username},
    )
    _settings_event(
        request, user,
        category="auth_provider",
        event_type="auth_provider.deleted",
        target_type="auth_provider",
        target_id=provider_id,
        target_label=name,
        message=f"Deleted identity provider '{name}'",
    )
    flash(request, f"Deleted identity provider '{name}'.", "success")
    return RedirectResponse(url="/ui/settings/auth-providers", status_code=303)


# ===========================================================================
# Branding
# ===========================================================================


def _get_or_create_branding(db: Session) -> AppBranding:
    """Return the singleton branding row, creating it from defaults if absent."""
    branding = db.get(AppBranding, BRANDING_ID)
    if branding is None:
        branding = AppBranding(
            id=BRANDING_ID,
            brand_name=branding_service.DEFAULT_NAME,
            brand_color="",
            icon_key=branding_service.DEFAULT_ICON,
        )
        db.add(branding)
        db.commit()
    return branding


@router.get("/branding")
def show_branding(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    branding = _get_or_create_branding(db)
    return render(
        request,
        "settings/branding.html",
        current_user=user,
        active_subsection="branding",
        form={
            "brand_name": branding.brand_name,
            "brand_color": branding.brand_color or branding_service.DEFAULT_COLOR,
            "icon_key": branding.icon_key,
        },
        icon_presets=branding_service.ICON_PRESETS,
        default_color=branding_service.DEFAULT_COLOR,
    )


@router.post("/branding")
def update_branding(
    request: Request,
    brand_name: str = Form(...),
    brand_color: str = Form(""),
    icon_key: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    brand_name = brand_name.strip()
    brand_color = brand_color.strip()
    icon_key = icon_key.strip()

    form = {
        "brand_name": brand_name,
        "brand_color": brand_color or branding_service.DEFAULT_COLOR,
        "icon_key": icon_key,
    }

    def _reject(message: str) -> Response:
        return render(
            request,
            "settings/branding.html",
            current_user=user,
            active_subsection="branding",
            form=form,
            icon_presets=branding_service.ICON_PRESETS,
            default_color=branding_service.DEFAULT_COLOR,
            error=message,
        )

    if not brand_name:
        return _reject("Brand name is required.")
    if len(brand_name) > 100:
        return _reject("Brand name must be 100 characters or fewer.")
    if brand_color and not _HEX_COLOR_RE.match(brand_color):
        return _reject("Color must be a hex value like #1e293b.")
    if icon_key not in branding_service.ICON_PRESETS:
        return _reject("Please choose one of the available icons.")

    # Store empty when the color matches the theme default so the app keeps
    # following the theme rather than pinning to a now-stale hex.
    if brand_color.lower() == branding_service.DEFAULT_COLOR.lower():
        brand_color = ""

    branding = _get_or_create_branding(db)
    branding.brand_name = brand_name
    branding.brand_color = brand_color
    branding.icon_key = icon_key
    db.commit()
    branding_service.invalidate()

    log.info(
        "ui_branding_updated",
        extra={"icon_key": icon_key, "has_color": bool(brand_color), "by": user.username},
    )
    _settings_event(
        request, user,
        category="branding",
        event_type="branding.updated",
        target_type="branding",
        target_label=brand_name,
        message=f"Updated branding to '{brand_name}'",
        detail={"icon_key": icon_key, "has_color": bool(brand_color)},
    )
    flash(request, "Branding updated.", "success")
    return RedirectResponse(url="/ui/settings/branding", status_code=303)


# ===========================================================================
# System settings
# ===========================================================================

# Upper bound on the retention window (~10 years) — a sanity guard, not a policy.
_MAX_RETENTION_DAYS = 3650


@router.get("/system")
def show_system(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    config = system_config.get_config(db)
    return render(
        request,
        "settings/system.html",
        current_user=user,
        active_subsection="system",
        form={"audit_retention_days": config.audit_retention_days},
    )


@router.post("/system")
def update_system(
    request: Request,
    audit_retention_days: int = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    if audit_retention_days < 0 or audit_retention_days > _MAX_RETENTION_DAYS:
        return render(
            request,
            "settings/system.html",
            current_user=user,
            active_subsection="system",
            form={"audit_retention_days": audit_retention_days},
            error=(
                f"Retention must be between 0 and {_MAX_RETENTION_DAYS} days "
                "(0 keeps events forever)."
            ),
        )

    previous = system_config.get_config(db).audit_retention_days
    system_config.set_retention_days(db, audit_retention_days)

    # Apply the new window immediately so lowering it takes effect now rather
    # than waiting for the next daily sweep.
    pruned = prune_old_events(audit_retention_days)

    _settings_event(
        request, user,
        category="system",
        event_type="system.settings.updated",
        target_type="app_config",
        message=f"Set audit retention to {audit_retention_days} day(s)",
        detail={
            "audit_retention_days": audit_retention_days,
            "previous": previous,
            "events_pruned": pruned,
        },
    )

    msg = f"Audit retention set to {audit_retention_days} day(s)."
    if audit_retention_days == 0:
        msg = "Audit retention disabled — events are now kept forever."
    if pruned:
        msg += f" Removed {pruned} event(s) older than the new window."
    flash(request, msg, "success")
    return RedirectResponse(url="/ui/settings/system", status_code=303)


# ===========================================================================
# Reset
# ===========================================================================


@router.get("/reset")
def show_reset(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "settings/reset.html",
        current_user=user,
        active_subsection="reset",
    )


@router.post("/reset")
def do_reset(
    request: Request,
    reset_employees: str | None = Form(None),
    reset_employment_statuses: str | None = Form(None),
    reset_departments_and_titles: str | None = Form(None),
    reset_locations: str | None = Form(None),
    reset_states_provinces: str | None = Form(None),
    reset_countries: str | None = Form(None),
    reset_audit_events: str | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    actions: list[str] = []

    do_employees = bool(reset_employees)
    do_statuses = bool(reset_employment_statuses)
    do_depts = bool(reset_departments_and_titles)
    do_locations = bool(reset_locations)
    do_states = bool(reset_states_provinces)
    do_countries = bool(reset_countries)
    do_audit = bool(reset_audit_events)

    # Dependency check: tables referenced by employees must be reset together
    if (do_statuses or do_depts) and not do_employees:
        flash(
            request,
            "Resetting employment statuses or departments/titles requires resetting employees too "
            "(employees reference them).",
            "error",
        )
        return RedirectResponse(url="/ui/settings/reset", status_code=303)
    if do_countries and not (do_states and do_employees):
        flash(
            request,
            "Resetting countries requires also resetting states/provinces and employees "
            "(they reference countries).",
            "error",
        )
        return RedirectResponse(url="/ui/settings/reset", status_code=303)
    if do_states and not do_employees:
        flash(
            request,
            "Resetting states/provinces requires also resetting employees (employees reference states).",
            "error",
        )
        return RedirectResponse(url="/ui/settings/reset", status_code=303)

    if not any(
        (do_employees, do_statuses, do_depts, do_locations, do_states, do_countries, do_audit)
    ):
        flash(request, "Nothing was selected to reset.", "warning")
        return RedirectResponse(url="/ui/settings/reset", status_code=303)

    # Order matters: wipe employees first, then the tables they reference
    try:
        if do_employees:
            n = seed_data.reset_employees(db, reseed_samples=True)
            actions.append(f"employees ({n})")

        if do_statuses:
            n = seed_data.reset_employment_statuses(db)
            actions.append(f"employment statuses ({n})")

        if do_depts:
            depts, titles = seed_data.reset_departments_and_titles(db)
            actions.append(f"departments ({depts}) and job titles ({titles})")

        if do_locations:
            n = seed_data.reset_locations(db)
            actions.append(f"locations ({n})")

        if do_states:
            n = seed_data.reset_states_provinces(db)
            actions.append(f"states/provinces ({n})")

        if do_countries:
            n = seed_data.reset_countries(db)
            actions.append(f"countries ({n})")

        # If we reset employees and statuses/depts/countries, we need to also
        # re-seed sample employees because their FKs were wiped out
        if do_employees and (do_statuses or do_depts or do_states or do_countries):
            seed_data.seed_sample_employees(db)

        # Clearing the audit log is independent of the other tables (no FKs).
        if do_audit:
            n = db.query(AuditEvent).delete()
            db.commit()
            actions.append(f"audit events ({n})")
    except Exception as exc:
        db.rollback()
        log.exception("ui_reset_failed", extra={"by": user.username})
        flash(request, f"Reset failed: {exc}", "error")
        return RedirectResponse(url="/ui/settings/reset", status_code=303)

    log.warning("ui_reset_completed", extra={"by": user.username, "actions": actions})
    # Record the reset itself — written after the wipe so it survives an audit clear
    # and documents that the log was cleared.
    _settings_event(
        request, user,
        category="system",
        event_type="system.data_reset",
        message="Reset demo data: " + ", ".join(actions),
        detail={"actions": actions},
    )
    flash(request, "Reset complete: " + ", ".join(actions) + ".", "success")
    return RedirectResponse(url="/ui/settings/reset", status_code=303)


# ===========================================================================
# Backup / Restore
# ===========================================================================


@router.get("/backup")
def show_backup(
    request: Request,
    user: AppUser = Depends(require_admin),
) -> Response:
    return render(
        request,
        "settings/backup.html",
        current_user=user,
        active_subsection="backup",
    )


@router.post("/backup/download")
def download_backup(
    request: Request,
    password: str = Form(""),
    user: AppUser = Depends(require_admin),
) -> Response:
    password = password or ""
    try:
        data, filename = backup_service.create_backup(password or None)
    except backup_service.BackupError as exc:
        flash(request, f"Backup failed: {exc}", "error")
        return RedirectResponse(url="/ui/settings/backup", status_code=303)

    log.info(
        "ui_backup_downloaded",
        extra={"encrypted": bool(password), "size": len(data), "by": user.username},
    )
    _settings_event(
        request, user,
        category="system",
        event_type="system.backup.created",
        message="Downloaded a backup",
        detail={"encrypted": bool(password), "size": len(data)},
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/backup/restore")
async def restore_backup(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(""),
    confirm: str = Form(""),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_admin),
) -> Response:
    if confirm.strip() != "RESTORE":
        flash(request, "Type RESTORE to confirm the restore.", "error")
        return RedirectResponse(url="/ui/settings/backup", status_code=303)

    file_bytes = await file.read()
    if not file_bytes:
        flash(request, "No backup file was uploaded.", "error")
        return RedirectResponse(url="/ui/settings/backup", status_code=303)

    # Release this request's DB connection before the restore swaps the database
    # file (and deletes its WAL) out from under it — an open handle over a
    # deleted WAL yields a SQLite "disk I/O error". `user` is already loaded, so
    # closing the session here is safe; record_event later opens its own on the
    # rebuilt engine.
    db.close()

    try:
        manifest = backup_service.restore_backup(file_bytes, password or None)
    except backup_service.BackupError as exc:
        flash(request, f"Restore failed: {exc}", "error")
        return RedirectResponse(url="/ui/settings/backup", status_code=303)

    log.warning("ui_backup_restored", extra={"by": user.username})
    # Recorded through record_event's own session, which binds to the freshly
    # rebuilt engine — the request's injected db session is now stale.
    record_event(
        category="system",
        event_type="system.backup.restored",
        actor_type="user",
        actor_label=user.username,
        actor_id=user.id,
        message="Restored data from a backup",
        detail={
            "surface": "ui",
            "app_version": manifest.get("app_version"),
            "alembic_revision": manifest.get("alembic_revision"),
        },
        request=request,
    )
    flash(
        request,
        "Restore complete. Restart the app to fully apply the restored session key; "
        "you may need to sign in again.",
        "success",
    )
    return RedirectResponse(url="/ui/settings/backup", status_code=303)
