"""Typed application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError
from sqlalchemy import URL

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration for the backend service."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    db_host: str = "127.0.0.1"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "paperpilot_dev"
    db_user: str = "paperpilot_app"
    db_password: SecretStr

    @property
    def database_url(self) -> URL:
        """Build an escaped SQLAlchemy URL without interpolating credentials."""

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"connect_timeout": "5"},
        )


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated runtime settings."""

    return Settings()


class AuthenticationConfigurationError(RuntimeError):
    """The authentication key is missing or invalid."""


class AuthSettings(BaseSettings):
    """Load authentication settings independently of database settings."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    jwt_secret_key: SecretStr = Field(repr=False)

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_secret_key(cls, secret: SecretStr) -> SecretStr:
        value = secret.get_secret_value()
        if re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            raise ValueError("JWT_SECRET_KEY must encode 32 random bytes as hexadecimal")
        # Reject obvious repetition. Only secure generation provides entropy.
        if len(set(bytes.fromhex(value))) < 20:
            raise ValueError("JWT_SECRET_KEY must be generated from random bytes")
        return secret


@lru_cache
def get_auth_settings() -> AuthSettings:
    """Validate and cache the JWT key on first authentication use."""
    try:
        return AuthSettings()
    except (ValidationError, SettingsError, OSError):
        pass
    raise AuthenticationConfigurationError("Authentication is unavailable") from None
