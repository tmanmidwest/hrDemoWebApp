"""Drift guard for the committed API reference.

The repo ships ``docs/openapi.json`` (and the rendered ``docs/api.html``) so a
developer has the complete API reference without a running instance. Those files
are generated from the code by ``scripts/export_openapi.py``. This test fails if
they've fallen out of sync — i.e. someone changed the API surface but didn't
regenerate the docs.

Fix a failure by running:

    python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_JSON = REPO_ROOT / "docs" / "openapi.json"


def _current_spec_json() -> str:
    """Serialize the live spec exactly as scripts/export_openapi.py does."""
    from app.main import create_app

    spec = create_app().openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_committed_openapi_matches_code() -> None:
    assert OPENAPI_JSON.exists(), (
        "docs/openapi.json is missing — run: python scripts/export_openapi.py"
    )
    committed = OPENAPI_JSON.read_text(encoding="utf-8")
    current = _current_spec_json()
    assert committed == current, (
        "docs/openapi.json is out of date with the code. "
        "Regenerate it with: python scripts/export_openapi.py"
    )


def test_api_html_is_offline_and_present() -> None:
    """The rendered reference must exist and carry no runtime network deps."""
    api_html = REPO_ROOT / "docs" / "api.html"
    redoc_js = REPO_ROOT / "docs" / "redoc.standalone.js"
    assert api_html.exists(), "docs/api.html is missing — run scripts/export_openapi.py"
    assert redoc_js.exists(), "docs/redoc.standalone.js (vendored Redoc bundle) is missing"

    html = api_html.read_text(encoding="utf-8")
    # It should load the vendored bundle locally, not from a CDN.
    assert "./redoc.standalone.js" in html
    assert "http://" not in html
    assert "https://" not in html
