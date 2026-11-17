"""Database readiness endpoint tests."""

import asyncio

from app.db.session import DatabaseUnavailableError
from app.main import app
from tests.test_health import request


def test_readiness_returns_200_when_database_check_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("app.main.check_database_connection", lambda: None)

    response = asyncio.run(request("GET", "/api/v1/ready"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ready"}


def test_readiness_returns_503_without_internal_details(monkeypatch) -> None:
    def unavailable() -> None:
        raise DatabaseUnavailableError("postgresql://user:secret@localhost/database")

    monkeypatch.setattr("app.main.check_database_connection", unavailable)

    response = asyncio.run(request("GET", "/api/v1/ready"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    assert "secret" not in response.text
