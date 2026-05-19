"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Give each test its own isolated data directory."""
    with tempfile.TemporaryDirectory(prefix="hrsot-test-") as tmp:
        path = Path(tmp)
        monkeypatch.setenv("HRSOT_DATA_DIR", str(path))
        # Clear the settings cache so fresh env vars are picked up
        from app.config import get_settings

        get_settings.cache_clear()
        # Reset module-level engine cache between tests
        import app.db as db_module

        db_module._engine = None  # type: ignore[attr-defined]
        db_module._SessionLocal = None  # type: ignore[attr-defined]
        yield path
        get_settings.cache_clear()
        db_module._engine = None  # type: ignore[attr-defined]
        db_module._SessionLocal = None  # type: ignore[attr-defined]


@pytest.fixture
def client() -> Iterator[object]:
    """Return a FastAPI TestClient bound to a fresh app instance."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_session(client):  # type: ignore[no-untyped-def]
    """Log in as the seeded admin via session. Returns the same client."""
    from app.config import get_settings

    settings = get_settings()
    resp = client.post(
        "/api/v1/auth/session/login",
        json={
            "username": settings.initial_admin_username,
            "password": settings.initial_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def api_key(admin_session):  # type: ignore[no-untyped-def]
    """Create an API key and return its plaintext value."""
    resp = admin_session.post(
        "/api/v1/auth/api-keys/", json={"name": "Test Suite Key"}
    )
    assert resp.status_code == 201
    return str(resp.json()["key"])


@pytest.fixture
def auth_headers(api_key: str) -> dict[str, str]:
    """Authorization header dict for REST API calls."""
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def api_client(client, auth_headers):  # type: ignore[no-untyped-def]
    """A TestClient with API key default headers."""
    client.headers.update(auth_headers)
    return client
