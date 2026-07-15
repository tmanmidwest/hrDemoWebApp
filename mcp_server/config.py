"""Configuration for the HR SoT MCP server.

Loaded from environment variables with the ``HRMCP_`` prefix, mirroring the main
app's ``HRSOT_`` convention. Every setting has a default so the container runs
out of the box, but each is overridable — which is what lets a dev instance and
a prod instance sit side by side on the same Docker host (distinct ports /
container names / upstream URLs).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MCP server settings from ``HRMCP_``-prefixed environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HRMCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Upstream HR SoT app ---
    hr_api_base_url: str = Field(
        default="http://hr-sot:8000",
        description=(
            "Base URL of the HR SoT app this server proxies to. Inside Docker "
            "Compose this is the service name (http://hr-sot:8000); point it at a "
            "different host/port to target a dev or prod instance."
        ),
    )
    request_timeout_seconds: float = Field(
        default=30.0, description="Per-request timeout when calling the HR API."
    )

    # --- Bind ---
    bind_host: str = Field(default="0.0.0.0", description="Host to bind the MCP server to.")
    bind_port: int = Field(default=8100, description="Port to bind the MCP server to.")
    path: str = Field(
        default="/mcp",
        description="URL path the streamable-HTTP endpoint is served at.",
    )

    # --- Meta ---
    server_name: str = Field(default="hrsot-mcp", description="MCP server name.")
    log_level: str = Field(default="INFO", description="DEBUG, INFO, WARNING, ERROR.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached MCP server settings."""
    return Settings()
