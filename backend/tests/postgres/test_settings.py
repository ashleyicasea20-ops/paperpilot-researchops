"""Synthetic configuration tests; no credentials, files or database are loaded."""

import traceback

import pytest

from tests.postgres.settings import PREFIX, PgSafetyError, PgTestSettings, load_settings, preflight


def test_independent_environment_sources(values, monkeypatch):
    monkeypatch.setenv("DB_NAME", "paperpilot_dev")
    monkeypatch.setenv("DB_PASSWORD", "unrelated-application-sentinel")
    for key, value in values.items():
        monkeypatch.setenv(PREFIX + key.upper(), str(value))
    settings = load_settings()
    assert settings.db_name == "paperpilot_concurrency_test"
    assert settings.password.get_secret_value() == values["password"]
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert settings.database_url.password == values["password"]
    assert settings.database_url.port == 55432
    assert values["password"] not in repr(settings) + str(settings.database_url)


@pytest.mark.parametrize("missing", ["enable", "host", "port", "db_name", "user", "password", "environment_id"])
def test_all_configuration_is_required(values, missing):
    values.pop(missing)
    with pytest.raises(PgSafetyError):
        PgTestSettings(**values)


@pytest.mark.parametrize("field,value", [
    ("enable", "true"), ("enable", 1), ("host", "localhost"),
    ("host", "127.0.0.1,127.0.0.2"), ("host", ""), ("host", "/tmp"),
    ("port", 5432), ("port", True), ("port", "055432"),
    ("db_name", "paperpilot_dev"), ("db_name", "paperpilot_migration_test"),
    ("db_name", "postgres"), ("db_name", "template0"), ("db_name", "template1"),
    ("user", "paperpilot_app"), ("user", "postgres"),
    ("password", ""), ("password", "  "),
    ("environment_id", ""), ("environment_id", "not-a-uuid"),
    ("environment_id", "00000000-0000-0000-0000-000000000000"),
    ("environment_id", "54C232B5-33AF-4DBC-85C7-72A0B7892074"),
])
def test_unapproved_values_rejected(values, field, value):
    values[field] = value
    with pytest.raises(PgSafetyError):
        PgTestSettings(**values)


@pytest.mark.parametrize("source", ["_env_file", "_secrets_dir", "_env_prefix"])
def test_sources_cannot_be_reenabled(values, source):
    with pytest.raises(PgSafetyError):
        PgTestSettings(**values, **{source: "never-open-this-file"})


def test_no_config_never_loads_application_settings(monkeypatch):
    import app.core.config as app_config
    from unittest.mock import Mock
    app_settings = Mock(side_effect=AssertionError("Application settings used"))
    monkeypatch.setattr(app_config, "get_settings", app_settings)
    with pytest.raises(PgSafetyError) as caught:
        load_settings()
    app_settings.assert_not_called()
    assert caught.value.__context__ is None


def test_error_and_repr_redaction(values):
    secret = values["password"]
    values["host"] = secret
    with pytest.raises(PgSafetyError) as caught:
        PgTestSettings(**values)
    output = str(caught.value) + repr(caught.value) + "".join(traceback.format_exception(caught.value))
    assert secret not in output
    assert caught.value.__context__ is None
    assert not hasattr(caught.value, "errors")


@pytest.mark.parametrize("name", ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
                                  "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSFILE"])
def test_libpq_implicit_sources_are_refused(settings, opt_in, name):
    with pytest.raises(PgSafetyError):
        preflight(settings, opt_in, environ={name: "synthetic"})


def test_model_copy_cannot_bypass_target_validation(settings, opt_in):
    forged = settings.model_copy(update={"db_name": "paperpilot_dev"})
    with pytest.raises(PgSafetyError):
        preflight(forged, opt_in)


def test_unprefixed_environment_is_not_a_test_configuration(values, monkeypatch):
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))
    with pytest.raises(PgSafetyError):
        load_settings()
