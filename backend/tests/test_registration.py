"""Registration API with isolated SQLite; PostgreSQL is never written here."""

import asyncio
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, TimeoutError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import verify_password
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.models import User
from app.services import registration

PATH = "/api/v1/auth/register"
PASSWORD = "synthetic-registration-passphrase"


@pytest.fixture(autouse=True)
def block_real_postgresql(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Registration tests must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", forbidden)
    monkeypatch.setattr("app.db.session.get_engine", forbidden)


@pytest.fixture
def database():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False}, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = []

    class TrackedSession(Session):
        rollback_calls = 0
        close_calls = 0

        def rollback(self):
            self.rollback_calls += 1
            return super().rollback()

        def close(self):
            self.close_calls += 1
            return super().close()

    factory = sessionmaker(bind=engine, class_=TrackedSession, expire_on_commit=False)

    def override():
        with factory() as session:
            sessions.append(session)
            yield session

    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_db_session] = override
    yield SimpleNamespace(engine=engine, factory=factory, sessions=sessions)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)
    engine.dispose()


def send(payload=None, *, method="POST", headers=None, content=None):
    async def perform():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.request(method, PATH, json=payload if content is None else None,
                                        content=content, headers=headers)
    return asyncio.run(perform())


def user_count(database):
    with Session(database.engine) as session:
        return session.scalar(select(func.count()).select_from(User))


def test_success_persists_only_hash_and_returns_public_fields(database, caplog):
    response = send({"email": " Researcher@Example.COM ", "password": PASSWORD})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "is_active", "created_at", "updated_at"}
    assert isinstance(UUID(body["id"]), UUID)
    assert body["email"] == "researcher@example.com" and body["is_active"] is True
    assert datetime.fromisoformat(body["created_at"]) and datetime.fromisoformat(body["updated_at"])
    assert user_count(database) == 1
    with Session(database.engine) as session:
        user = session.get(User, UUID(body["id"]))
        encoded = user.password_hash
        assert encoded != PASSWORD and encoded.startswith("$argon2id$")
        assert len(encoded) <= 255 and verify_password(PASSWORD, encoded)
    assert PASSWORD not in response.text and encoded not in response.text
    assert PASSWORD not in caplog.text and encoded not in caplog.text
    assert all(s.close_calls for s in database.sessions)


@pytest.mark.parametrize("email", ["Alice@Example.com", "alice@example.com", "ALICE@EXAMPLE.COM"])
def test_case_variants_conflict_at_database_index(database, email):
    assert send({"email": " Alice@Example.COM ", "password": PASSWORD}).status_code == 201
    response = send({"email": email, "password": PASSWORD})
    assert response.status_code == 409 and response.json() == {"detail": "Email is already registered"}
    assert user_count(database) == 1
    assert database.sessions[-1].rollback_calls >= 1 and database.sessions[-1].close_calls >= 1


@pytest.mark.parametrize("email", ["", "   ", "not-an-email", "user@localhost", "猫@example.com", "user@例子.com", "user@exämple.com", "Display <user@example.com>", "a" * 321 + "@example.com", None, 42, ["user@example.com"]])
def test_invalid_email_is_safe_and_not_hashed(database, monkeypatch, email):
    monkeypatch.setattr(registration, "hash_password", Mock(side_effect=AssertionError("Hasher must not run")))
    response = send({"email": email, "password": PASSWORD})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid registration input"}
    assert PASSWORD not in response.text and user_count(database) == 0
    registration.hash_password.assert_not_called()


def test_plus_tag_and_dots_are_preserved(database):
    for email in ("First.Last+Tag@Example.com", "FirstLast+Tag@Example.com", "First.Last@Example.com"):
        response = send({"email": email, "password": PASSWORD})
        assert response.status_code == 201 and response.json()["email"] == email.lower()
    assert user_count(database) == 3


@pytest.mark.parametrize("password", ["x" * 15, "x" * 128, "猫" * 15, "😀" * 128, " " * 15, "  no strip allowed  "])
def test_valid_password_boundaries_unicode_and_no_composition_requirement(database, password):
    response = send({"email": "boundaries@example.com", "password": password})
    assert response.status_code == 201
    with Session(database.engine) as session:
        encoded = session.scalar(select(User.password_hash))
        assert verify_password(password, encoded)
        if password != password.strip():
            assert not verify_password(password.strip(), encoded)


@pytest.mark.parametrize("password", ["x" * 14, "x" * 129, "😀" * 257, None, 123, True, [PASSWORD], {"private": PASSWORD}])
def test_invalid_password_type_length_and_byte_limit_are_not_echoed(database, monkeypatch, password, caplog):
    hasher = Mock(side_effect=AssertionError("Hasher must not run"))
    monkeypatch.setattr(registration, "hash_password", hasher)
    response = send({"email": "invalid-password@example.com", "password": password})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid registration input"}
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert user_count(database) == 0
    hasher.assert_not_called()


@pytest.mark.parametrize("payload", [{"email": "missing@example.com"}, {"password": PASSWORD}, {"email": "valid@example.com", "password": PASSWORD, PASSWORD: PASSWORD}, [PASSWORD], PASSWORD])
def test_missing_extra_and_invalid_body_errors_have_no_input_ctx_or_messages(database, payload):
    response = send(payload)
    assert response.status_code == 422 and response.json() == {"detail": "Invalid registration input"}
    assert PASSWORD not in response.text
    assert user_count(database) == 0


@pytest.mark.parametrize("content", [b'{"password":"private-malformed-sentinel",', json.dumps({"email": "invalid@example.com", "password": "\ud800" * 15}).encode("ascii")])
def test_malformed_json_and_invalid_unicode_are_safe(database, content):
    response = send(content=content, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid registration input"}
    assert "private-malformed-sentinel" not in response.text


def test_commit_failure_rolls_back_flushed_user(database, monkeypatch, caplog):
    def fail_commit(self):
        raise OperationalError(None, {"password": "private-db-sentinel"}, RuntimeError("private-db-sentinel"))

    monkeypatch.setattr(database.factory.class_, "commit", fail_commit)
    response = send({"email": "rollback@example.com", "password": PASSWORD})
    assert response.status_code == 503 and response.json() == {"detail": "Database is unavailable"}
    assert user_count(database) == 0
    assert database.sessions[-1].rollback_calls >= 1 and database.sessions[-1].close_calls >= 1
    assert "private-db-sentinel" not in response.text and "private-db-sentinel" not in caplog.text


def test_non_email_integrity_error_is_500_and_rolls_back(database, monkeypatch):
    def invalid_hash(self):
        for user in self.new:
            user.password_hash = ""
        return original(self)

    cls = database.factory.class_
    original = cls.flush
    monkeypatch.setattr(cls, "flush", invalid_hash)
    response = send({"email": "constraint@example.com", "password": PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Registration failed"}
    assert user_count(database) == 0 and database.sessions[-1].rollback_calls >= 1


@pytest.mark.parametrize("failure_kind", ["settings", "connection", "timeout", "programming", "unexpected"])
def test_dependency_creation_errors_are_sanitized(database, monkeypatch, caplog, failure_kind):
    marker = "private-dependency-sentinel"
    failures = {
        "settings": ValidationError.from_exception_data("Settings", [{"type": "int_parsing", "loc": ("db_port",), "input": marker}]),
        "connection": OperationalError(None, {"password": marker}, RuntimeError(marker)),
        "timeout": TimeoutError(marker),
        "programming": ProgrammingError(marker, None, RuntimeError(marker)),
        "unexpected": RuntimeError(marker),
    }

    def unavailable():
        raise failures[failure_kind]

    app.dependency_overrides.pop(get_db_session)
    monkeypatch.setattr("app.db.session.get_session_factory", unavailable)
    response = send({"email": "dependency@example.com", "password": PASSWORD})
    expected = 503 if failure_kind in {"settings", "connection", "timeout"} else 500
    assert response.status_code == expected
    assert marker not in response.text and marker not in caplog.text and PASSWORD not in response.text


@pytest.mark.parametrize("sqlstate,index,status", [("23505", "uq_users_email_lower", 409), ("23505", "pk_users", 500), ("23514", "uq_users_email_lower", 500), ("23505", None, 500)])
def test_postgresql_diagnostic_classification_without_real_connection(database, monkeypatch, sqlstate, index, status, caplog):
    marker = "private-driver-sentinel"
    original = Exception(marker)
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(constraint_name=index)
    failure = IntegrityError(None, {"password": marker}, original)
    session = Mock(spec=Session)
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    users = []
    session.add.side_effect = users.append

    def flush():
        users[0].id = uuid4()
        users[0].created_at = users[0].updated_at = datetime.now(timezone.utc)
        users[0].is_active = True

    session.flush.side_effect = flush
    session.commit.side_effect = failure

    def override():
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override
    monkeypatch.setattr(registration, "hash_password", lambda _: "$argon2id$synthetic-only")
    response = send({"email": "driver@example.com", "password": PASSWORD})
    assert response.status_code == status
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    assert marker not in response.text and marker not in caplog.text
    session.execute.assert_not_called()  # no prequery pretending to prevent races


def test_original_dependency_closes_session_and_internal_hash_failure_rolls_back(database, monkeypatch):
    session = database.factory()
    app.dependency_overrides.pop(get_db_session)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(registration, "hash_password", Mock(side_effect=RuntimeError("private-hash-sentinel")))
    response = send({"email": "hash-failure@example.com", "password": PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Registration failed"}
    assert session.rollback_calls >= 1 and session.close_calls >= 1
    assert user_count(database) == 0


def test_response_validation_failure_rolls_back_before_commit(database, monkeypatch):
    def invalid_response(*args, **kwargs):
        raise ValueError("private-response-sentinel")

    monkeypatch.setattr(registration.UserResponse, "model_validate", invalid_response)
    response = send({"email": "response-failure@example.com", "password": PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Registration failed"}
    assert user_count(database) == 0
    assert database.sessions[-1].rollback_calls >= 1 and database.sessions[-1].close_calls >= 1


def test_rollback_failure_still_closes_original_dependency(database, monkeypatch, caplog):
    session = Mock(spec=Session)
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    session.rollback.side_effect = RuntimeError("private-rollback-sentinel")
    app.dependency_overrides.pop(get_db_session)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(registration, "hash_password", Mock(side_effect=RuntimeError("private-hash-sentinel")))
    response = send({"email": "rollback-failure@example.com", "password": PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Registration failed"}
    session.close.assert_called_once()
    assert session.rollback.call_count >= 1
    assert "private-rollback-sentinel" not in response.text + caplog.text


def test_dependency_close_failure_is_sanitized_before_response(database, monkeypatch, caplog):
    session = Mock(spec=Session)
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    session.close.side_effect = RuntimeError("private-close-sentinel")
    app.dependency_overrides.pop(get_db_session)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(registration, "hash_password", Mock(side_effect=RuntimeError("private-hash-sentinel")))
    response = send({"email": "close-failure@example.com", "password": PASSWORD})
    assert response.status_code == 500 and response.json() == {"detail": "Registration failed"}
    assert "private-close-sentinel" not in response.text + caplog.text


def test_post_cors_preserves_origin_allowlist_without_credentials(database):
    headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    response = send(method="OPTIONS", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in response.headers
    response = send(method="OPTIONS", headers={**headers, "Origin": "https://untrusted.example"})
    assert response.status_code == 400 and "access-control-allow-origin" not in response.headers
