"""Configuration for the HR SoT MCP server.

Loaded from environment variables with the ``HRMCP_`` prefix, mirroring the main
app's ``HRSOT_`` convention. Every setting has a default so the container runs
out of the box, but each is overridable — which is what lets a dev instance and
a prod instance sit side by side on the same Docker host (distinct ports /
container names / upstream URLs).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    # --- Shared data volume (token files written by the app) ---
    data_dir: Path = Field(
        default=Path("/data"),
        description=(
            "Path where the app writes the MCP token files this server reads: "
            "the outbound service key (mcp_api_key) and the active inbound gateway "
            "token hashes (mcp_gateway_tokens.json). Mount the app's data volume "
            "here (read-only is fine)."
        ),
    )

    # --- Outbound service token (server → app), managed in the app UI ---
    api_key: str | None = Field(
        default=None,
        description=(
            "Static outbound API key override. If set, used instead of the "
            "UI-managed mcp_api_key file — for a remote MCP host that can't see "
            "the data volume."
        ),
    )
    api_key_file: str | None = Field(
        default=None,
        description="Path to a file holding the outbound API key (overrides the default location).",
    )

    # --- Inbound gateway auth (client → server), managed in the app UI ---
    auth_token: str | None = Field(
        default=None,
        description=(
            "Static inbound bearer-token override. If set, accepted in addition "
            "to the UI-managed gateway tokens — for a remote host that can't see "
            "the data volume."
        ),
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
