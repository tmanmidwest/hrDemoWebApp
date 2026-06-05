"""UI routes for settings: admin users, API keys, OAuth clients, reset."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey, AppUser, OAuthClient
from app.services import seed_data
from app.services.passwords import hash_password
from app.services.tokens import (
    generate_api_key,
    generate_oauth_client_id,
    generate_oauth_client_secret,
    hash_token,
)
from app.ui.dependencies import require_ui_user
from app.ui.flash import flash
from app.ui.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/settings", tags=["ui"], include_in_schema=False)


# ===========================================================================
# Admin users
# ===========================================================================


@router.get("/admin-users")
def list_admins(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    users = db.query(AppUser).order_by(AppUser.username).all()
    return render(
        request,
        "settings/admin_users.html",
        current_user=user,
        active_subsection="admin_users",
        users=users,
    )


@router.get("/admin-users/new")
def show_new_admin(
    request: Request,
    user: AppUser = Depends(require_ui_user),
) -> Response:
    return render(
        request,
        "settings/admin_user_new.html",
        current_user=user,
        active_subsection="admin_users",
        form={},
    )


@router.post("/admin-users/new")
def create_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    username = username.strip()
    if len(password) < 8:
        return render(
            request,
            "settings/admin_user_new.html",
            current_user=user,
            active_subsection="admin_users",
            form={"username": username},
            error="Password must be at least 8 characters.",
        )

    new_user = AppUser(
        username=username,
        password_hash=hash_password(password),
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
            form={"username": username},
            error=f"Username '{username}' is already taken.",
        )
    log.info(
        "ui_admin_created",
        extra={"target_user_id": new_user.id, "target_username": username, "by": user.username},
    )
    flash(request, f"Admin user '{username}' created.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.get("/admin-users/{user_id}/password")
def show_password_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
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
    user: AppUser = Depends(require_ui_user),
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
    flash(request, f"Password updated for {target.username}.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


@router.post("/admin-users/{user_id}/delete")
def delete_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
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
    flash(request, f"Deleted admin user '{target.username}'.", "success")
    return RedirectResponse(url="/ui/settings/admin-users", status_code=303)


# ===========================================================================
# API Keys
# ===========================================================================


@router.get("/api-keys")
def list_api_keys(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
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
    )


@router.get("/api-keys/new")
def show_new_api_key(
    request: Request,
    user: AppUser = Depends(require_ui_user),
) -> Response:
    return render(
        request,
        "settings/api_key_new.html",
        current_user=user,
        active_subsection="api_keys",
    )


@router.post("/api-keys/new")
def create_api_key(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    full_key, prefix = generate_api_key()
    key = ApiKey(
        name=name.strip(),
        key_prefix=prefix,
        key_hash=hash_token(full_key),
        created_by_user_id=user.id,
    )
    db.add(key)
    db.commit()
    log.info(
        "ui_api_key_created",
        extra={"api_key_id": key.id, "key_name": key.name, "prefix": prefix, "by": user.username},
    )
    # Stash the full key in session so the list page can reveal it once
    request.session["_revealed_api_key"] = full_key
    return RedirectResponse(url="/ui/settings/api-keys", status_code=303)


@router.post("/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        db.commit()
        log.info("ui_api_key_revoked", extra={"api_key_id": key_id, "by": user.username})
        flash(request, f"Revoked API key '{key.name}'.", "success")
    return RedirectResponse(url="/ui/settings/api-keys", status_code=303)


@router.post("/api-keys/{key_id}/delete")
def delete_api_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    name = key.name
    db.delete(key)
    db.commit()
    log.info("ui_api_key_deleted", extra={"api_key_id": key_id, "by": user.username})
    flash(request, f"Deleted API key '{name}'.", "success")
    return RedirectResponse(url="/ui/settings/api-keys", status_code=303)


# ===========================================================================
# OAuth Clients
# ===========================================================================


@router.get("/oauth-clients")
def list_oauth_clients(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
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
    user: AppUser = Depends(require_ui_user),
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
    user: AppUser = Depends(require_ui_user),
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
    user: AppUser = Depends(require_ui_user),
) -> Response:
    client = db.get(OAuthClient, client_pk)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth client not found.")
    if client.revoked_at is None:
        client.revoked_at = datetime.now(UTC)
        db.commit()
        log.info("ui_oauth_client_revoked", extra={"oauth_client_id": client_pk, "by": user.username})
        flash(request, f"Revoked OAuth client '{client.name}'.", "success")
    return RedirectResponse(url="/ui/settings/oauth-clients", status_code=303)


@router.post("/oauth-clients/{client_pk}/delete")
def delete_oauth_client(
    client_pk: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    client = db.get(OAuthClient, client_pk)
    if client is None:
        raise HTTPException(status_code=404, detail="OAuth client not found.")
    name = client.name
    db.delete(client)
    db.commit()
    log.info("ui_oauth_client_deleted", extra={"oauth_client_id": client_pk, "by": user.username})
    flash(request, f"Deleted OAuth client '{name}'.", "success")
    return RedirectResponse(url="/ui/settings/oauth-clients", status_code=303)


# ===========================================================================
# Reset
# ===========================================================================


@router.get("/reset")
def show_reset(
    request: Request,
    user: AppUser = Depends(require_ui_user),
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
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_ui_user),
) -> Response:
    actions: list[str] = []

    do_employees = bool(reset_employees)
    do_statuses = bool(reset_employment_statuses)
    do_depts = bool(reset_departments_and_titles)
    do_locations = bool(reset_locations)
    do_states = bool(reset_states_provinces)
    do_countries = bool(reset_countries)

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

    if not any((do_employees, do_statuses, do_depts, do_locations, do_states, do_countries)):
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
    except Exception as exc:
        db.rollback()
        log.exception("ui_reset_failed", extra={"by": user.username})
        flash(request, f"Reset failed: {exc}", "error")
        return RedirectResponse(url="/ui/settings/reset", status_code=303)

    log.warning("ui_reset_completed", extra={"by": user.username, "actions": actions})
    flash(request, "Reset complete: " + ", ".join(actions) + ".", "success")
    return RedirectResponse(url="/ui/settings/reset", status_code=303)
