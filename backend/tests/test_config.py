"""Application configuration tests."""

from app.core.config import Settings


def test_database_settings_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5544")
    monkeypatch.setenv("DB_NAME", "paperpilot_test")
    monkeypatch.setenv("DB_USER", "app_user")
    monkeypatch.setenv("DB_PASSWORD", "p@ss:/?#[]")

    settings = Settings(_env_file=None)
    url = settings.database_url

    assert settings.db_host == "db.internal"
    assert settings.db_port == 5544
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "app_user"
    assert url.password == "p@ss:/?#[]"
    assert url.database == "paperpilot_test"
    assert url.query["connect_timeout"] == "5"
    assert "p%40ss" in url.render_as_string(hide_password=False)
    assert "***" in url.render_as_string()
