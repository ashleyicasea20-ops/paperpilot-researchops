"""Isolated fixtures for the Stage 03B authentication tests."""

import asyncio
from functools import partial
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.config import AuthSettings, get_auth_settings
from app.core.security import hash_password
from app.db.base import Base
from app.main import app
from app.models import User

AUTH_PASSWORD = "  Synthetic-Passphrase 猫  "


@pytest.fixture(scope="session")
def auth_settings():
    key = sha256(b"PaperPilot synthetic pytest signing key").hexdigest()
    return AuthSettings(jwt_secret_key=key, _env_file=None)


@pytest.fixture
def auth_database(monkeypatch, auth_settings):
    def forbid_postgresql(*args, **kwargs):
        raise AssertionError("Authentication tests must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", forbid_postgresql)
    monkeypatch.setattr("app.db.session.get_engine", forbid_postgresql)

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        connect_args={"check_same_thread": False},
        hide_parameters=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    monkeypatch.setitem(app.dependency_overrides, get_auth_settings, lambda: auth_settings)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def auth_client(auth_database):
    def send(method, path, *, asgi_app=app, **options):
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url="http://testserver") as client:
                return await client.request(method, path, **options)

        return asyncio.run(request())

    return SimpleNamespace(get=partial(send, "GET"), post=partial(send, "POST"), options=partial(send, "OPTIONS"))


@pytest.fixture
def protected_app(auth_database, auth_settings):
    api = FastAPI()
    api.dependency_overrides[get_auth_settings] = lambda: auth_settings
    return api


@pytest.fixture(scope="session")
def auth_password_hash():
    return hash_password(AUTH_PASSWORD)


@pytest.fixture
def auth_user(auth_database, auth_password_hash):
    with auth_database.begin() as session:
        user = User(email="Researcher@Example.COM", password_hash=auth_password_hash)
        session.add(user)
    return user
