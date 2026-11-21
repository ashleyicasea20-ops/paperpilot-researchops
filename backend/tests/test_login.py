"""Login API tests with synthetic credentials and in-memory SQLite only."""

import json
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from argon2.exceptions import VerificationError
from argon2.low_level import error_to_str, lib
from fastapi import Request
from pwdlib import PasswordHash
from pwdlib.exceptions import HashVerificationError
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import config
from app.core.token import AUDIENCE, ISSUER, read_access_token
from app.db import session as db
from app.main import app
from app.models import User
from app.services import authentication
from tests.conftest import AUTH_PASSWORD

LOGIN = "/api/v1/auth/login"


@pytest.mark.parametrize("email", ["researcher@example.com", " Researcher@EXAMPLE.com "])
def test_existing_user_login_is_read_only(auth_client, auth_user, auth_database, auth_settings, email):
    statements = []

    @event.listens_for(auth_database.kw["bind"], "before_cursor_execute")
    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    response = auth_client.post(LOGIN, json={"email": email, "password": AUTH_PASSWORD})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert read_access_token(body["access_token"], auth_settings) == auth_user.id
    assert auth_user.password_hash.startswith("$argon2id$")
    claims = jwt.decode(
        body["access_token"], bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value()),
        algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
    )
    assert claims["sub"] == str(auth_user.id) and claims["exp"] - claims["iat"] == 900
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
    assert "lower(users.email)" in statements[0]


def test_stage_03a_registration_can_immediately_log_in(auth_client):
    credentials = {"email": "registered@example.com", "password": AUTH_PASSWORD}
    registration = auth_client.post("/api/v1/auth/register", json=credentials)
    assert registration.status_code == 201
    login = auth_client.post(LOGIN, json=credentials)
    assert login.status_code == 200
    me = auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200 and me.json() == registration.json()


def test_login_accepts_existing_password_below_registration_minimum(auth_client, auth_database):
    email = "legacy@example.com"
    password = " Ab "
    with auth_database.begin() as session:
        session.add(User(email=email, password_hash=PasswordHash.recommended().hash(password)))
    assert auth_client.post(LOGIN, json={"email": email, "password": password}).status_code == 200
    assert auth_client.post(LOGIN, json={"email": email, "password": password.strip()}).status_code == 401
    assert auth_client.post(LOGIN, json={"email": email, "password": password.lower()}).status_code == 401


@pytest.mark.parametrize("reason", ["absent", "wrong-password", "disabled"])
def test_bad_credentials_share_one_contract_and_verify_once(
    auth_client, auth_user, auth_database, monkeypatch, caplog, reason,
):
    email, password = auth_user.email, AUTH_PASSWORD
    if reason == "absent":
        email = "absent@example.com"
    elif reason == "wrong-password":
        password = "synthetic-wrong-password"
    else:
        with auth_database.begin() as session:
            session.get(User, auth_user.id).is_active = False

    verify = Mock(wraps=authentication.verify_password)
    monkeypatch.setattr(authentication, "verify_password", verify)
    response = auth_client.post(LOGIN, json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    expected_hash = auth_user.password_hash if reason == "wrong-password" else authentication.DUMMY_PASSWORD_HASH
    verify.assert_called_once_with(password, expected_hash)
    assert password not in response.text + caplog.text
    assert expected_hash not in response.text + caplog.text


@pytest.mark.parametrize("credentials", [
    {"email": "not-an-email", "password": AUTH_PASSWORD},
    {"email": "unicode-é@example.com", "password": AUTH_PASSWORD},
    {"password": AUTH_PASSWORD},
    {"email": "test@example.com"},
    {"email": "test@example.com", "password": {"secret": AUTH_PASSWORD}},
    {"email": "test@example.com", "password": 123},
    {"email": "test@example.com", "password": ""},
    {"email": "test@example.com", "password": "x" * 1025},
    {"email": "test@example.com", "password": "😀" * 257},
    {"email": "test@example.com", "password": "\ud800"},
])
def test_invalid_input_is_sanitized_before_verification(auth_client, monkeypatch, caplog, credentials):
    verify = Mock(side_effect=AssertionError("Argon2 should not run"))
    monkeypatch.setattr(authentication, "verify_password", verify)

    response = auth_client.post(LOGIN, content=json.dumps(credentials), headers={"Content-Type": "application/json"})

    assert response.status_code == 422 and response.json() == {"detail": "Invalid login input"}
    assert AUTH_PASSWORD not in response.text + caplog.text
    verify.assert_not_called()


@pytest.mark.parametrize("body", [b'{"password":"synthetic-parser-input",', b'{"password":"synthetic-parser-input\xff"}'])
def test_malformed_json_is_sanitized(auth_client, monkeypatch, caplog, body):
    verify = Mock(side_effect=AssertionError("Argon2 should not run"))
    monkeypatch.setattr(authentication, "verify_password", verify)
    response = auth_client.post(LOGIN, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid login input"}
    assert "synthetic-parser-input" not in response.text + caplog.text
    verify.assert_not_called()


def test_internal_parser_error_is_sanitized(auth_client, monkeypatch, caplog):
    marker = "synthetic-parser-runtime-details"
    parser = AsyncMock(side_effect=RuntimeError(marker))
    monkeypatch.setattr(Request, "json", parser)
    response = auth_client.post(LOGIN, json={"email": "test@example.com", "password": AUTH_PASSWORD})
    parser.assert_awaited_once()
    assert response.status_code == 500 and response.json() == {"detail": "Authentication failed"}
    assert marker not in response.text + caplog.text and AUTH_PASSWORD not in response.text + caplog.text


def test_database_outage_is_not_an_authentication_rejection(auth_client, monkeypatch, caplog):
    marker = "synthetic-database-diagnostics"

    def error():
        raise OperationalError(f"SELECT {marker}", {"secret": marker}, ConnectionError(marker))

    monkeypatch.setattr(db, "get_session_factory", error)
    response = auth_client.post(LOGIN, json={"email": "test@example.com", "password": AUTH_PASSWORD})
    assert response.status_code == 503 and response.json() == {"detail": "Database is unavailable"}
    assert marker not in response.text + caplog.text and AUTH_PASSWORD not in response.text + caplog.text


@pytest.mark.parametrize("jwt_key", [None, "synthetic-weak-key"])
def test_missing_or_weak_jwt_configuration_does_not_break_health(
    auth_client, monkeypatch, caplog, jwt_key,
):
    real_auth_settings = config.AuthSettings
    monkeypatch.setattr(config, "AuthSettings", lambda: real_auth_settings(_env_file=None))
    monkeypatch.delitem(app.dependency_overrides, config.get_auth_settings)
    if jwt_key is None:
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET_KEY", jwt_key)
    monkeypatch.setattr("app.main.check_database_connection", lambda: None)
    open_session = Mock(side_effect=AssertionError("No session expected"))
    monkeypatch.setattr(db, "get_session_factory", open_session)
    config.get_auth_settings.cache_clear()
    try:
        response = auth_client.post(LOGIN, json={"email": "test@example.com", "password": AUTH_PASSWORD})
        assert response.status_code == 503 and response.json() == {"detail": "Authentication is unavailable"}
        me = auth_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})
        assert me.status_code == 503 and me.json() == response.json()
        assert auth_client.get("/api/v1/health").status_code == 200
        assert auth_client.get("/api/v1/ready").status_code == 200
        open_session.assert_not_called()
    finally:
        config.get_auth_settings.cache_clear()
    if jwt_key is not None:
        assert jwt_key not in response.text + me.text + caplog.text


def test_signing_failure_is_sanitized(auth_client, auth_user, monkeypatch, caplog):
    marker = "synthetic-signing-secret"
    monkeypatch.setattr(jwt, "encode", Mock(side_effect=RuntimeError(marker)))
    response = auth_client.post(LOGIN, json={"email": auth_user.email, "password": AUTH_PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Authentication failed"}
    assert marker not in response.text + caplog.text and AUTH_PASSWORD not in response.text + caplog.text


def test_session_close_failure_is_sanitized_before_token_response(
    auth_client, auth_user, auth_database, monkeypatch, caplog,
):
    marker = "synthetic-session-close-secret"
    close = Mock(side_effect=RuntimeError(marker))
    session = auth_database()
    invalidate = Mock(wraps=session.invalidate)
    verify = Mock(wraps=authentication.verify_password)
    sign = Mock(wraps=jwt.encode)
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(session, "invalidate", invalidate)
    monkeypatch.setattr(authentication, "verify_password", verify)
    monkeypatch.setattr(jwt, "encode", sign)
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    try:
        response = auth_client.post(LOGIN, json={"email": auth_user.email, "password": AUTH_PASSWORD})
        assert response.status_code == 500 and response.json() == {"detail": "Authentication failed"}
        close.assert_called_once_with()
        assert marker not in response.text + caplog.text and AUTH_PASSWORD not in response.text + caplog.text
        invalidate.assert_called_once_with()
        assert auth_database.kw["bind"].pool.checkedout() == 0
        verify.assert_not_called()
        sign.assert_not_called()
    finally:
        Session.close(session)


def test_query_outage_survives_rollback_failure(auth_client, auth_database, monkeypatch, caplog):
    marker = "synthetic-query-rollback-secret"
    engine = auth_database.kw["bind"]
    session = auth_database()
    rollback = Mock(side_effect=RuntimeError(marker))
    close = Mock(wraps=session.close)
    monkeypatch.setattr(session, "rollback", rollback)
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)

    @event.listens_for(engine, "before_cursor_execute", once=True)
    def fail_query(connection, cursor, statement, parameters, context, executemany):
        raise OperationalError(statement, {"secret": marker}, ConnectionError(marker))

    response = auth_client.post(LOGIN, json={"email": "outage@example.com", "password": AUTH_PASSWORD})

    rollback.assert_called_once_with()
    close.assert_called_once_with()
    assert engine.pool.checkedout() == 0
    assert response.status_code == 503 and response.json() == {"detail": "Database is unavailable"}
    assert "Authentication session rollback failed" in caplog.messages
    assert marker not in response.text + caplog.text and AUTH_PASSWORD not in response.text + caplog.text


@pytest.mark.parametrize("encoded", [
    "synthetic-unrecognized-credential",
    "$2b$12$synthetic-unsupported-credential",
    "$argon2id$v=19$m=65536,t=3,p=4$synthetic-invalid-base64$bad!hash",
    "$argon2id$v=19$m=65536,t=3,p=4$YQ$YQ",
])
def test_corrupt_hash_uses_dummy_and_matches_unknown_account(
    auth_client, auth_database, auth_user, monkeypatch, caplog, encoded,
):
    with auth_database.begin() as session:
        session.get(User, auth_user.id).password_hash = encoded
    verify = Mock(wraps=authentication.verify_password)
    monkeypatch.setattr(authentication, "verify_password", verify)

    unknown = auth_client.post(LOGIN, json={"email": "absent@example.com", "password": AUTH_PASSWORD})
    verify.reset_mock()
    response = auth_client.post(LOGIN, json={"email": auth_user.email, "password": AUTH_PASSWORD})

    assert response.status_code == unknown.status_code == 401
    assert response.json() == unknown.json() == {"detail": "Invalid credentials"}
    assert response.headers["WWW-Authenticate"] == unknown.headers["WWW-Authenticate"] == "Bearer"
    assert [call.args[1] for call in verify.call_args_list] == [encoded, authentication.DUMMY_PASSWORD_HASH]
    assert "Stored password hash could not be verified" in caplog.messages
    assert encoded not in response.text + caplog.text and AUTH_PASSWORD not in response.text + caplog.text
    with auth_database() as session:
        assert session.get(User, auth_user.id).password_hash == encoded


@pytest.mark.parametrize("fault", ["runtime", "argon2-resource"])
def test_unexpected_verifier_failure_remains_server_error(auth_client, auth_user, monkeypatch, caplog, fault):
    marker = "synthetic-verifier-internal-secret"

    def fail(*args):
        if fault == "argon2-resource":
            error = VerificationError(error_to_str(lib.ARGON2_MEMORY_ALLOCATION_ERROR))
            raise HashVerificationError(marker) from error
        raise RuntimeError(marker)

    verify = Mock(side_effect=fail)
    monkeypatch.setattr(authentication, "verify_password", verify)
    response = auth_client.post(LOGIN, json={"email": auth_user.email, "password": AUTH_PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Authentication failed"}
    verify.assert_called_once()
    assert marker not in response.text + caplog.text


@pytest.mark.parametrize("account", ["active", "unknown", "disabled"])
def test_login_releases_real_connection_before_verification(
    auth_client, auth_database, auth_user, monkeypatch, account,
):
    email = auth_user.email
    if account == "unknown":
        email = "unknown@example.com"
    elif account == "disabled":
        with auth_database.begin() as session:
            session.get(User, auth_user.id).is_active = False

    engine = auth_database.kw["bind"]
    activity = []
    statements = []
    verify = authentication.verify_password

    @event.listens_for(engine, "before_cursor_execute")
    def record_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    @event.listens_for(engine, "commit")
    def record_commit(connection):
        activity.append("commit")

    @event.listens_for(engine.pool, "checkout")
    def record_checkout(dbapi_connection, record, proxy):
        activity.append("checkout")

    @event.listens_for(engine.pool, "checkin")
    def record_checkin(dbapi_connection, record):
        activity.append("checkin")

    checks = []

    def verify_with_pool_check(password, encoded):
        checks.append((engine.pool.checkedout(), encoded == authentication.DUMMY_PASSWORD_HASH))
        activity.append("verify")
        return verify(password, encoded)

    monkeypatch.setattr(authentication, "verify_password", verify_with_pool_check)
    response = auth_client.post(LOGIN, json={"email": email, "password": AUTH_PASSWORD})

    assert response.status_code == (200 if account == "active" else 401)
    assert checks == [(0, account != "active")]
    assert activity == ["checkout", "checkin", "verify"]
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
