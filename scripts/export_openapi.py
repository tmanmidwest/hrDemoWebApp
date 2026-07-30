#!/usr/bin/env python3
"""Export the app's OpenAPI spec to committed files under ``docs/``.

The live ``/docs`` and ``/redoc`` pages are just renderers over the spec that
FastAPI generates from the code. This script writes that same spec to
``docs/openapi.json`` and ``docs/openapi.yaml`` so a developer has the *complete*
API reference in the repo — every endpoint, parameter, and schema — without
needing a running instance.

Run it whenever the API surface changes:

    python scripts/export_openapi.py

It writes three files under ``docs/``:

* ``openapi.json`` / ``openapi.yaml`` — the raw spec (importable into Postman,
  usable for client codegen).
* ``api.html`` — a browsable reference rendered with Redoc. It loads the
  **vendored** ``docs/redoc.standalone.js`` (no CDN) and has the spec inlined,
  so it opens fully offline by double-clicking the file — no server, no network.

The spec files are written with sorted keys and a trailing newline so re-runs
produce minimal, reviewable diffs. ``tests/test_openapi_docs.py`` fails if the
committed spec falls out of sync with the code.

Note: ``redoc.standalone.js`` is a one-time vendored asset (~0.9 MB). To refresh
it, re-download the pinned Redoc bundle into ``docs/`` — see README.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"

# The Redoc bundle vendored next to the generated page (referenced locally so
# the page needs no network). Kept out of the generated api.html so we don't
# duplicate ~0.9 MB of JS into the HTML on every export.
REDOC_JS_FILENAME = "redoc.standalone.js"

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title} — API Reference (v{version})</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="description" content="Offline REST API reference for {title}." />
  <style>body {{ margin: 0; padding: 0; }}</style>
</head>
<body>
  <div id="redoc-container"></div>
  <!-- Vendored locally — no CDN, works offline. -->
  <script src="./{redoc_js}"></script>
  <script>
    // Spec is inlined so this single page renders with no server or network.
    var spec = {spec_json};
    Redoc.init(spec, {{ hideDownloadButton: false, expandResponses: "200,201" }},
      document.getElementById("redoc-container"));
  </script>
</body>
</html>
"""


def _write_api_html(spec: dict) -> Path | None:
    """Render docs/api.html with the spec inlined, if the Redoc bundle is present."""
    if not (DOCS_DIR / REDOC_JS_FILENAME).exists():
        print(
            f"NOTE: docs/{REDOC_JS_FILENAME} not found — skipping api.html. "
            "Download the pinned Redoc bundle into docs/ to enable it (see README).",
            file=sys.stderr,
        )
        return None

    info = spec.get("info", {})
    # Compact JSON, with the "</" sequence escaped so a description containing
    # "</script>" can't break out of the inline <script> block.
    spec_json = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
    html = _HTML_TEMPLATE.format(
        title=info.get("title", "API"),
        version=info.get("version", ""),
        redoc_js=REDOC_JS_FILENAME,
        spec_json=spec_json,
    )
    html_path = DOCS_DIR / "api.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def build_spec() -> dict:
    """Build the FastAPI app in isolation and return its OpenAPI spec.

    Importing ``app.main`` constructs the app, which writes a session secret to
    the data dir. We point that at a throwaway temp dir so the export never
    touches (or requires) ``/data`` and has no side effects on a real instance.
    """
    tmp = tempfile.mkdtemp(prefix="hrsot-openapi-")
    os.environ.setdefault("HRSOT_DATA_DIR", tmp)

    # Import lazily, after the env var is set.
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    return app.openapi()


def main() -> int:
    spec = build_spec()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = DOCS_DIR / "openapi.json"
    yaml_path = DOCS_DIR / "openapi.yaml"

    json_text = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    json_path.write_text(json_text, encoding="utf-8")

    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a project dep
        print("PyYAML is required (pip install pyyaml).", file=sys.stderr)
        return 1

    yaml_text = yaml.safe_dump(spec, sort_keys=True, allow_unicode=True, width=100)
    yaml_path.write_text(yaml_text, encoding="utf-8")

    html_path = _write_api_html(spec)

    op_count = sum(
        1
        for methods in spec.get("paths", {}).values()
        for _ in methods
    )
    written = f"{json_path.relative_to(REPO_ROOT)}, {yaml_path.relative_to(REPO_ROOT)}"
    if html_path is not None:
        written += f", {html_path.relative_to(REPO_ROOT)}"
    print(
        f"Wrote {written} "
        f"({op_count} operations across {len(spec.get('paths', {}))} paths, "
        f"spec version {spec.get('info', {}).get('version')})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
