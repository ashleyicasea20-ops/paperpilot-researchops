"""Isolated fixtures for the Stage 03B authentication tests."""

import asyncio
import sqlite3
from functools import partial
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.config import AuthSettings, get_auth_settings
from app.core.security import hash_password
from app.db.base import Base
from app.main import app
from app.models import User, Workspace, WorkspaceMember
from app.core.token import issue_access_token

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

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

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

    return SimpleNamespace(
        get=partial(send, "GET"), post=partial(send, "POST"), options=partial(send, "OPTIONS"),
        patch=partial(send, "PATCH"), delete=partial(send, "DELETE"), put=partial(send, "PUT"),
    )


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


@pytest.fixture
def workspace_database(auth_database, monkeypatch):
    """Shared *memory* URI survives pool invalidation for cleanup fault tests."""
    uri = "file:workspace_" + uuid4().hex + "?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True, check_same_thread=False)
    engine = create_engine(
        "sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False),
        poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=0.5, hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    try:
        yield factory
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()
        keeper.close()


@pytest.fixture
def workspace_tenants(workspace_database, auth_settings):
    users = {name: uuid4() for name in "ABCDEF"}
    spaces = {name: uuid4() for name in "AB"}
    with workspace_database.begin() as session:
        session.add_all([
            User(id=uid, email="workspace-" + name.lower() + "@example.com", password_hash="synthetic-workspace-placeholder")
            for name, uid in users.items()
        ])
        session.flush()
        session.add_all([Workspace(id=wid, owner_id=users[name], name="Workspace " + name) for name, wid in spaces.items()])
        session.flush()
        session.add_all([
            WorkspaceMember(workspace_id=spaces["A"], user_id=users["C"], role="admin"),
            WorkspaceMember(workspace_id=spaces["A"], user_id=users["D"], role="editor"),
            WorkspaceMember(workspace_id=spaces["A"], user_id=users["E"], role="viewer"),
            WorkspaceMember(workspace_id=spaces["B"], user_id=users["C"], role="viewer"),
        ])
    tokens = {name: issue_access_token(uid, auth_settings) for name, uid in users.items()}
    return SimpleNamespace(users=users, spaces=spaces, tokens=tokens)


@pytest.fixture
def workspace_api(workspace_tenants, auth_client):
    def request(method, *, user="A", workspace=None, **options):
        path = "/api/v1/workspaces"
        if workspace is not None:
            path += "/" + str(workspace_tenants.spaces.get(workspace, workspace))
        options.setdefault("headers", {} if user is None else {"Authorization": "Bearer " + workspace_tenants.tokens[user]})
        return getattr(auth_client, method.lower())(path, **options)
    return request


@pytest.fixture
def member_api(workspace_tenants, auth_client):
    def request(method, *, user="A", workspace="A", target=None, **options):
        workspace_id = workspace_tenants.spaces.get(workspace, workspace)
        path = "/api/v1/workspaces/" + str(workspace_id) + "/members"
        if target is not None:
            path += "/" + str(workspace_tenants.users.get(target, target))
        options.setdefault("headers", {} if user is None else {"Authorization": "Bearer " + workspace_tenants.tokens[user]})
        return getattr(auth_client, method.lower())(path, **options)
    return request
