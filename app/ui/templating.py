"""Jinja2 templates configuration and shared context helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import __version__
from app.models import AppUser
from app.services.branding import current_branding
from app.ui.flash import get_flashes

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _asset_version() -> str:
    """Cache-busting token for static assets — the newest mtime of app.css/app.js.

    Appended as ``?v=`` to the stylesheet/script links so a rebuilt image always
    serves fresh CSS/JS instead of a browser-cached copy. Changes automatically
    whenever either file changes; falls back to the app version if unreadable.
    """
    try:
        mtimes = [
            (_STATIC_DIR / name).stat().st_mtime
            for name in ("app.css", "app.js")
            if (_STATIC_DIR / name).exists()
        ]
        if mtimes:
            return str(int(max(mtimes)))
    except OSError:
        pass
    return __version__


def render(
    request: Request,
    template_name: str,
    *,
    current_user: AppUser | None = None,
    **context: Any,
) -> Any:
    """Render a template with common context (current user, flashes, version, etc.).

    Always pull flashes into the context so the base layout can render them.
    """
    base_context: dict[str, Any] = {
        "request": request,
        "current_user": current_user,
        "flashes": get_flashes(request),
        "app_version": __version__,
        "asset_version": _asset_version(),
        "branding": current_branding(),
        "active_section": context.pop("active_section", None),
        "active_subsection": context.pop("active_subsection", None),
        "page_title": context.pop("page_title", None),
    }
    base_context.update(context)
    return templates.TemplateResponse(
        request=request, name=template_name, context=base_context
    )
