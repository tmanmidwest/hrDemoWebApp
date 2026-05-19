"""Smoke tests for the meta endpoints (/, /health, /docs)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_root_returns_app_metadata(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert "app" in body
    assert "version" in body
    assert body["docs"] == "/docs"
    assert body["health"] == "/health"


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "version" in body


def test_swagger_docs_accessible(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_openapi_spec_accessible(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"]
    assert spec["info"]["version"]
