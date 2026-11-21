"""Bearer current-user API tests with isolated SQLite and synthetic tokens."""

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.token import AUDIENCE, ISSUER, issue_access_token
from app.db import session as db
from app.main import app
from app.models import User

ME = "/api/v1/auth/me"


def authorization(token):
    return {"Authorization": f"Bearer {token}"}


def assert_unauthenticated(response):
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Invalid access token"}


def test_me_checks_stored_user_at_request_time(auth_client, auth_user, auth_database, auth_settings, caplog):
    token = issue_access_token(auth_user.id, auth_settings)
    with auth_database.begin() as session:
        session.get(User, auth_user.id).email = "updated@example.com"

    response = auth_client.get(ME, headers=authorization(token))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"id", "email", "is_active", "created_at", "updated_at"}
    assert response.json()["id"] == str(auth_user.id)
    assert response.json()["email"] == "updated@example.com" and response.json()["is_active"] is True
    assert auth_user.password_hash not in response.text + caplog.text
    assert token not in response.text + caplog.text


@pytest.mark.parametrize("header", [None, "Basic other", "Bearer", "Bearer abc.def.ghi", "Bearer abc.def.ghi extra"])
def test_missing_or_malformed_bearer_never_opens_database_session(auth_client, monkeypatch, header):
    open_session = Mock(side_effect=AssertionError("Invalid token must not open a session"))
    monkeypatch.setattr(db, "get_session_factory", open_session)
    headers = {"Authorization": header} if header is not None else {}
    assert_unauthenticated(auth_client.get(ME, headers=headers))
    open_session.assert_not_called()


@pytest.mark.parametrize("problem", ["expired", "wrong-key"])
def test_expired_or_wrong_signature_token_is_rejected(
    auth_client, auth_user, auth_settings, monkeypatch, caplog, problem,
):
    key = bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value())
    now = int(datetime.now(timezone.utc).timestamp())
    issued = now - 901 if problem == "expired" else now
    claims = {
        "sub": str(auth_user.id), "iss": ISSUER, "aud": AUDIENCE, "token_type": "access",
        "iat": issued, "nbf": issued, "exp": issued + 900,
    }
    if problem == "wrong-key":
        key = bytes(byte ^ 1 for byte in key)
    token = jwt.encode(claims, key, algorithm="HS256")
    open_session = Mock(side_effect=AssertionError("Invalid token must not open a session"))
    monkeypatch.setattr(db, "get_session_factory", open_session)
    response = auth_client.get(ME, headers=authorization(token))
    assert_unauthenticated(response)
    assert token not in response.text + caplog.text
    open_session.assert_not_called()


def test_unknown_user_is_rejected(auth_client, auth_settings):
    token = issue_access_token(uuid4(), auth_settings)
    assert_unauthenticated(auth_client.get(ME, headers=authorization(token)))


def test_disabling_user_rejects_an_existing_access_token(auth_client, auth_user, auth_database, auth_settings):
    token = issue_access_token(auth_user.id, auth_settings)
    assert auth_client.get(ME, headers=authorization(token)).status_code == 200
    with auth_database.begin() as session:
        session.get(User, auth_user.id).is_active = False
    assert_unauthenticated(auth_client.get(ME, headers=authorization(token)))


def test_me_database_failure_is_sanitized(auth_client, auth_user, auth_settings, monkeypatch, caplog):
    token = issue_access_token(auth_user.id, auth_settings)

    def broken_session():
        raise OperationalError("SELECT synthetic-diagnostic", {"credential": token}, ConnectionError(token))

    monkeypatch.setattr(db, "get_session_factory", broken_session)
    response = auth_client.get(ME, headers=authorization(token))
    assert response.status_code == 503 and response.json() == {"detail": "Database is unavailable"}
    assert token not in response.text + caplog.text
    assert "synthetic-diagnostic" not in response.text + caplog.text


def test_unexpected_jwt_decoder_failure_is_sanitized(auth_client, auth_user, auth_settings, monkeypatch, caplog):
    token = issue_access_token(auth_user.id, auth_settings)
    monkeypatch.setattr(jwt, "decode", Mock(side_effect=RuntimeError(token)))
    response = auth_client.get(ME, headers=authorization(token))
    assert response.status_code == 500 and response.json() == {"detail": "Authentication failed"}
    assert token not in response.text + caplog.text


def test_me_cors_preflight_allows_local_bearer_request(auth_client):
    response = auth_client.options(ME, headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-headers"].lower() == "authorization"
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("variant", ["identical", "valid-first", "invalid-first", "merged", "case-variant"])
def test_ambiguous_authorization_headers_are_rejected_before_db_access(
    auth_client, auth_user, auth_settings, monkeypatch, caplog, variant,
):
    token = issue_access_token(auth_user.id, auth_settings)
    valid = ("Authorization", f"Bearer {token}")
    invalid = ("Authorization", "Basic synthetic-header-marker")
    headers = {
        "identical": [valid, valid],
        "valid-first": [valid, invalid],
        "invalid-first": [invalid, valid],
        "merged": [("Authorization", f"Bearer {token}, Bearer {token}")],
        "case-variant": [valid, ("aUtHoRiZaTiOn", valid[1])],
    }
    assert auth_client.get(ME, headers=[valid]).status_code == 200
    open_session = Mock(wraps=db.get_session_factory)
    monkeypatch.setattr(db, "get_session_factory", open_session)

    response = auth_client.get(ME, headers=headers[variant])

    assert_unauthenticated(response)
    open_session.assert_not_called()
    assert token not in response.text + caplog.text
    assert "synthetic-header-marker" not in response.text + caplog.text


def test_unknown_user_rejection_survives_close_and_invalidate_failure(
    auth_client, auth_database, auth_settings, monkeypatch, caplog,
):
    marker = "synthetic-me-cleanup-secret"
    token = issue_access_token(uuid4(), auth_settings)
    session = auth_database()
    close = Mock(side_effect=RuntimeError(marker))
    invalidate = Mock(side_effect=RuntimeError(marker))
    rollback = Mock(wraps=session.rollback)
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(session, "invalidate", invalidate)
    monkeypatch.setattr(session, "rollback", rollback)
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    starts = []

    async def observe_response(scope, receive, send):
        async def record_start(message):
            if message["type"] == "http.response.start":
                starts.append(message["status"])
            await send(message)

        await app(scope, receive, record_start)

    try:
        response = auth_client.get(ME, headers=authorization(token), asgi_app=observe_response)
        assert_unauthenticated(response)
        assert starts == [401]
        rollback.assert_called_once_with()
        close.assert_called_once_with()
        invalidate.assert_called_once_with()
        cleanup_logs = [record for record in caplog.records if record.name == db.__name__]
        assert [record.getMessage() for record in cleanup_logs] == [
            "Authentication session close failed",
            "Authentication session invalidation failed",
        ]
        assert all(record.levelname == "WARNING" and record.exc_info is None for record in cleanup_logs)
        assert marker not in response.text + caplog.text
        assert token not in response.text + caplog.text
    finally:
        Session.close(session)
