"""Verify reusable authentication through a test-only FastAPI router."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from fastapi import APIRouter
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentIdentity
from app.api.protected_route import ProtectedRoute
from app.core import config
from app.core.token import AUDIENCE, ISSUER, issue_access_token
from app.db import session as db
from app.models import User
from app.services.authentication import AuthenticatedIdentity

IDENTITY_PATH = "/auth-probe"


@pytest.fixture
def identity_app(protected_app):
    router = APIRouter(route_class=ProtectedRoute)
    protected_app.state.handler_calls = 0

    @router.get(IDENTITY_PATH)
    def identity(current: CurrentIdentity) -> AuthenticatedIdentity:
        protected_app.state.handler_calls += 1
        return current

    protected_app.include_router(router)
    return protected_app


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _assert_invalid(response):
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid access token"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"


def test_identity_is_immutable_and_contains_only_trusted_uuid(identity_app, auth_client, auth_user, auth_settings):
    token = issue_access_token(auth_user.id, auth_settings)
    response = auth_client.get(IDENTITY_PATH, headers=_bearer(token), params={"user_id": str(uuid4())}, asgi_app=identity_app)
    assert response.status_code == 200
    assert response.json() == {"user_id": str(auth_user.id)}
    assert response.headers["Cache-Control"] == "no-store"
    assert identity_app.state.handler_calls == 1
    with pytest.raises(FrozenInstanceError):
        AuthenticatedIdentity(auth_user.id).user_id = uuid4()


@pytest.mark.parametrize("problem", ["missing", "wrong-signature", "expired", "invalid-uuid", "duplicate", "merged"])
def test_invalid_credentials_stop_before_database(identity_app, auth_client, auth_settings, monkeypatch, caplog, problem):
    now = int(datetime.now(timezone.utc).timestamp())
    key = bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value())
    issued = now - 901 if problem == "expired" else now
    claims = {
        "sub": "synthetic-invalid-uuid" if problem == "invalid-uuid" else str(uuid4()),
        "iss": ISSUER, "aud": AUDIENCE, "token_type": "access",
        "iat": issued, "nbf": issued, "exp": issued + 900,
    }
    if problem == "wrong-signature":
        key = bytes(byte ^ 1 for byte in key)
    token = jwt.encode(claims, key, algorithm="HS256")
    authorization = f"Bearer {token}"
    headers = _bearer(token)
    if problem == "duplicate":
        headers = [("Authorization", authorization), ("authorization", authorization)]
    elif problem == "merged":
        headers = {"Authorization": f"{authorization}, {authorization}"}
    elif problem == "missing":
        headers = {}
    open_session = Mock(side_effect=AssertionError("Invalid credentials must not open a Session"))
    monkeypatch.setattr(db, "get_session_factory", open_session)

    response = auth_client.get(IDENTITY_PATH, headers=headers, asgi_app=identity_app)

    _assert_invalid(response)
    open_session.assert_not_called()
    assert identity_app.state.handler_calls == 0
    assert token not in response.text + caplog.text


def test_identity_rejects_unknown_user(identity_app, auth_client, auth_settings):
    token = issue_access_token(uuid4(), auth_settings)
    _assert_invalid(auth_client.get(IDENTITY_PATH, headers=_bearer(token), asgi_app=identity_app))


def test_identity_rechecks_account_after_token_issuance(identity_app, auth_client, auth_user, auth_settings, auth_database):
    token = issue_access_token(auth_user.id, auth_settings)
    headers = _bearer(token)
    assert auth_client.get(IDENTITY_PATH, headers=headers, asgi_app=identity_app).status_code == 200
    with auth_database.begin() as session:
        session.get(User, auth_user.id).is_active = False
    _assert_invalid(auth_client.get(IDENTITY_PATH, headers=headers, asgi_app=identity_app))
    assert identity_app.state.handler_calls == 1


def test_identity_database_failure_is_redacted(identity_app, auth_client, auth_user, auth_settings, monkeypatch, caplog):
    token = issue_access_token(auth_user.id, auth_settings)
    diagnostic = "synthetic-db-diagnostic"

    def failed_factory():
        raise OperationalError(f"SELECT {diagnostic}", {"credential": token}, ConnectionError(diagnostic))

    monkeypatch.setattr(db, "get_session_factory", failed_factory)
    response = auth_client.get(IDENTITY_PATH, headers=_bearer(token), asgi_app=identity_app)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    assert response.headers["Cache-Control"] == "no-store"
    assert identity_app.state.handler_calls == 0
    assert token not in response.text + caplog.text and diagnostic not in response.text + caplog.text


@pytest.mark.parametrize("known_user", [True, False])
def test_identity_cleanup_does_not_return_unverified_success(
    identity_app, auth_client, auth_user, auth_settings, auth_database, monkeypatch, caplog, known_user,
):
    token = issue_access_token(auth_user.id if known_user else uuid4(), auth_settings)
    diagnostic = "synthetic-cleanup-diagnostic"
    session = auth_database()
    close = Mock(side_effect=RuntimeError(diagnostic))
    invalidate = Mock(side_effect=RuntimeError(diagnostic))
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(session, "invalidate", invalidate)
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    try:
        response = auth_client.get(IDENTITY_PATH, headers=_bearer(token), asgi_app=identity_app)
        if known_user:
            assert response.status_code == 500 and response.json() == {"detail": "Request failed"}
        else:
            _assert_invalid(response)
        close.assert_called_once_with()
        invalidate.assert_called_once_with()
        assert identity_app.state.handler_calls == 0
        assert token not in response.text + caplog.text and diagnostic not in response.text + caplog.text
    finally:
        Session.close(session)


def test_identity_missing_key_never_reads_database(identity_app, auth_client, auth_user, auth_settings, monkeypatch):
    token = issue_access_token(auth_user.id, auth_settings)
    settings_class = config.AuthSettings
    monkeypatch.setattr(config, "AuthSettings", lambda: settings_class(_env_file=None))
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delitem(identity_app.dependency_overrides, config.get_auth_settings)
    open_session = Mock(side_effect=AssertionError("Missing key must not open a Session"))
    monkeypatch.setattr(db, "get_session_factory", open_session)
    config.get_auth_settings.cache_clear()
    try:
        response = auth_client.get(IDENTITY_PATH, headers=_bearer(token), asgi_app=identity_app)
        assert response.status_code == 503
        assert response.json() == {"detail": "Authentication is unavailable"}
        open_session.assert_not_called()
        assert identity_app.state.handler_calls == 0
    finally:
        config.get_auth_settings.cache_clear()
