"""Application configuration loaded from environment variables.

All settings are optional with sensible defaults. The HRSOT_ prefix is required
on every environment variable to avoid collisions.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HRSOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---
    data_dir: Path = Field(
        default=Path("/data"),
        description="Directory where the SQLite DB and signing keys live.",
    )

    # --- Bind ---
    bind_host: str = Field(default="0.0.0.0", description="Host to bind the HTTP server to.")
    bind_port: int = Field(default=8000, description="Port to bind the HTTP server to.")

    # --- Logging ---
    log_level: str = Field(
        default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR."
    )

    # --- Auth ---
    session_secret: str | None = Field(
        default=None,
        description="Cookie signing secret. Auto-generated and persisted if not set.",
    )
    session_max_age_seconds: int = Field(
        default=8 * 60 * 60,  # 8 hours
        description="Session cookie max age in seconds.",
    )
    initial_admin_username: str = Field(
        default="robbytheadmin", description="Username for the seeded admin account."
    )
    initial_admin_password: str = Field(
        default="N0nPr0dF0r$@viynt8",
        description="Default password for the seeded admin account.",
    )

    # --- OAuth / JWT ---
    oauth_default_token_lifetime_seconds: int = Field(
        default=3600,
        description="Default lifetime for OAuth access tokens.",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm.")

    # --- App metadata ---
    app_name: str = Field(default="Demo HR Source of Truth App")
    app_version: str = Field(default="0.1.0")

    # --- Computed paths ---

    @property
    def database_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.data_dir / "hrsot.db"

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection string for the SQLite database."""
        return f"sqlite:///{self.database_path}"

    @property
    def jwt_signing_key_path(self) -> Path:
        """Path to the JWT signing key file."""
        return self.data_dir / "jwt_signing_key"

    @property
    def session_secret_path(self) -> Path:
        """Path to the persisted session secret file."""
        return self.data_dir / "session_secret"

    @property
    def initial_credentials_path(self) -> Path:
        """Path to the initial credentials marker file."""
        return self.data_dir / "INITIAL_CREDENTIALS.txt"

    # --- Helpers ---

    def ensure_data_dir(self) -> None:
        """Create the data directory if it does not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create_session_secret(self) -> str:
        """Return the configured session secret, or generate and persist a new one."""
        if self.session_secret:
            return self.session_secret
        self.ensure_data_dir()
        if self.session_secret_path.exists():
            return self.session_secret_path.read_text().strip()
        new_secret = secrets.token_urlsafe(48)
        self.session_secret_path.write_text(new_secret)
        self.session_secret_path.chmod(0o600)
        return new_secret

    def get_or_create_jwt_signing_key(self) -> str:
        """Return the JWT signing key, generating and persisting it if missing."""
        self.ensure_data_dir()
        if self.jwt_signing_key_path.exists():
            return self.jwt_signing_key_path.read_text().strip()
        new_key = secrets.token_urlsafe(48)
        self.jwt_signing_key_path.write_text(new_key)
        self.jwt_signing_key_path.chmod(0o600)
        return new_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
