"""Independent, fail-closed settings. No application defaults or dotenv sources."""

from dataclasses import dataclass
import os
from typing import Literal, Mapping
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PREFIX = "PAPERPILOT_PG_TEST_"


from tests.postgres.errors import PgSafetyError


@dataclass(frozen=True)
class PgOptIn:
    pg_test: bool = False
    pg_fault: bool = False
    enable: str | None = None
    fault_enable: str | None = None

    def require(self, *, fault: bool = False) -> None:
        if self.pg_test is not True or self.enable != "1":
            raise PgSafetyError("PostgreSQL tests require CLI and environment opt-in")
        if fault and (self.pg_fault is not True or self.fault_enable != "1"):
            raise PgSafetyError("PostgreSQL fault tests require separate opt-in")


class PgTestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, env_prefix=PREFIX, extra="forbid", frozen=True, populate_by_name=True,
        case_sensitive=True, hide_input_in_errors=True,
    )

    enable: Literal["1"] = Field(validation_alias=PREFIX + "ENABLE")
    host: Literal["127.0.0.1"] = Field(validation_alias=PREFIX + "HOST")
    port: int = Field(validation_alias=PREFIX + "PORT")
    db_name: Literal["paperpilot_concurrency_test"] = Field(validation_alias=PREFIX + "DB_NAME")
    user: Literal["paperpilot_test_app"] = Field(validation_alias=PREFIX + "USER")
    password: SecretStr = Field(validation_alias=PREFIX + "PASSWORD", repr=False)
    environment_id: str = Field(validation_alias=PREFIX + "ENVIRONMENT_ID")

    def __init__(self, **values):
        # Reject caller attempts to re-enable dotenv, secrets directories or other
        # BaseSettings control arguments before a source can open a file.
        forbidden = any(key.startswith("_") and not (key == "_env_file" and value is None)
                        for key, value in values.items())
        values.pop("_env_file", None)
        if forbidden:
            raise PgSafetyError("Test settings sources cannot be overridden")
        failed = False
        try:
            super().__init__(_env_file=None, _secrets_dir=None, **values)
        except Exception:
            failed = True
        if failed:
            raise PgSafetyError("Invalid or missing PostgreSQL test configuration")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return init_settings, env_settings

    @field_validator("port", mode="before")
    @classmethod
    def approved_port(cls, value):
        if type(value) not in (str, int) or str(value) != "55432":
            raise ValueError("Unapproved test port")
        return 55432

    @field_validator("password")
    @classmethod
    def password_required(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("A test password is required")
        return value

    @field_validator("environment_id")
    @classmethod
    def canonical_environment_id(cls, value):
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("A canonical UUID4 environment ID is required")
        return value

    @property
    def database_url(self):
        from sqlalchemy import URL
        return URL.create("postgresql+psycopg", username=self.user,
                          password=self.password.get_secret_value(), host=self.host,
                          port=self.port, database=self.db_name)


def load_settings() -> PgTestSettings:
    failed = False
    try:
        return PgTestSettings()
    except Exception:
        failed = True
    if failed:
        raise PgSafetyError("Invalid or missing PostgreSQL test configuration")


def preflight(settings: PgTestSettings, opt_in: PgOptIn, *,
              fault: bool = False, environ: Mapping[str, str] | None = None) -> None:
    opt_in.require(fault=fault)
    failed = False
    try:
        # Revalidate even a model_construct/model_copy result.
        validated = PgTestSettings(**settings.model_dump(warnings=False))
        if type(settings) is not PgTestSettings or validated != settings:
            raise ValueError("Noncanonical settings instance")
    except Exception:
        failed = True
    if failed:
        raise PgSafetyError("Unapproved PostgreSQL test target")
    # libpq has many implicit sources (including service files and PGOPTIONS).
    # Reject inherited PG* settings rather than quietly overriding some of them.
    if any(key.startswith("PG") for key in (os.environ if environ is None else environ)):
        raise PgSafetyError("Implicit libpq environment settings are forbidden")
