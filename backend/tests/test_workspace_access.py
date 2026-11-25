"""Test workspace isolation in SQLite and through test-only protected routes."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Annotated
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.dependencies import workspace_permission
from app.api.protected_route import ProtectedRoute
from app.core.authorization import Permission, WorkspaceAccessContext, WorkspaceRole
from app.core.token import AUDIENCE, ISSUER, InvalidAccessToken, issue_access_token
from app.db import session as db
from app.main import app
from app.models import User, Workspace, WorkspaceMember
from app.services.workspace_access import WorkspaceNotFound, resolve_workspace_access, require_workspace_permission

PREFIX = "/workspace-probe"


@pytest.fixture
def tenants(auth_database, auth_password_hash):
    users = {name: uuid4() for name in "ABCDEF"}
    workspaces = {name: uuid4() for name in "AB"}
    with auth_database.begin() as session:
        session.add_all([
            User(id=user_id, email=f"user-{name.lower()}@example.com", password_hash=auth_password_hash)
            for name, user_id in users.items()
        ])
        session.add_all([
            Workspace(id=workspace_id, owner_id=users[name], name=f"Workspace {name}")
            for name, workspace_id in workspaces.items()
        ])
        session.add_all([
            WorkspaceMember(workspace_id=workspaces["A"], user_id=users["C"], role="admin"),
            WorkspaceMember(workspace_id=workspaces["A"], user_id=users["D"], role="editor"),
            WorkspaceMember(workspace_id=workspaces["A"], user_id=users["E"], role="viewer"),
            WorkspaceMember(workspace_id=workspaces["B"], user_id=users["C"], role="viewer"),
        ])
    return users, workspaces


@pytest.fixture
def workspace_app(protected_app):
    router = APIRouter(route_class=ProtectedRoute)
    protected_app.state.handler_calls = 0

    def register(permission):
        @router.get(f"{PREFIX}/{{workspace_id}}/{permission.value}")
        def probe(access: Annotated[WorkspaceAccessContext, Depends(workspace_permission(permission))]):
            protected_app.state.handler_calls += 1
            return {"workspace_id": str(access.workspace_id), "user_id": str(access.user_id), "role": access.effective_role}

    for permission in Permission:
        register(permission)
    protected_app.include_router(router)
    return protected_app


@pytest.fixture
def workspace_request(auth_client, auth_settings, tenants, workspace_app):
    users, workspaces = tenants

    def request(user="A", workspace="A", permission=Permission.WORKSPACE_READ, *, token=None, **options):
        workspace_id = workspaces.get(workspace, workspace)
        if token is None and user is not None:
            token = issue_access_token(users[user], auth_settings)
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return auth_client.get(f"{PREFIX}/{workspace_id}/{permission.value}", headers=headers, asgi_app=workspace_app, **options)

    return request


@pytest.mark.parametrize(("user", "workspace", "expected"), [
    ("A", "A", "owner"), ("B", "B", "owner"),
    ("C", "A", "admin"), ("C", "B", "viewer"), ("D", "A", "editor"), ("E", "A", "viewer"),
])
def test_workspace_scoped_roles(workspace_request, tenants, user, workspace, expected):
    users, workspaces = tenants
    response = workspace_request(user, workspace)
    assert response.status_code == 200
    assert response.json() == {"workspace_id": str(workspaces[workspace]), "user_id": str(users[user]), "role": expected}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(("user", "workspace", "permission", "status"), [
    ("A", "A", Permission.WORKSPACE_DELETE, 200),
    ("C", "A", Permission.WORKSPACE_UPDATE, 200), ("C", "A", Permission.WORKSPACE_DELETE, 403),
    ("C", "B", Permission.WORKSPACE_UPDATE, 403), ("C", "A", Permission.MEMBER_MANAGE_ADMIN, 403),
    ("D", "A", Permission.RESOURCE_CREATE, 200), ("D", "A", Permission.RESOURCE_DELETE, 403),
    ("D", "A", Permission.MEMBER_MANAGE_BASIC, 403),
    ("E", "A", Permission.RESOURCE_READ, 200), ("E", "A", Permission.RESOURCE_UPDATE, 403),
])
def test_access_checked_against_route_permission(workspace_request, user, workspace, permission, status):
    response = workspace_request(user, workspace, permission)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    if status == 403:
        assert response.json() == {"detail": "Insufficient workspace permissions"}


def test_missing_workspace_and_nonmembers_are_indistinguishable(workspace_request, workspace_app):
    for response in (workspace_request("A", uuid4()), workspace_request("B"), workspace_request("F"), workspace_request("A", "B")):
        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace not found"}
        assert response.headers["Cache-Control"] == "no-store"
    assert workspace_app.state.handler_calls == 0


def test_client_cannot_override_subject_role_or_required_permission(workspace_request, tenants):
    users, _ = tenants
    response = workspace_request("F", params={"user_id": str(users["A"]), "subject": str(users["A"]), "role": "owner"})
    assert response.status_code == 404
    response = workspace_request("E", permission=Permission.WORKSPACE_DELETE, params={"permission": "workspace:read"})
    assert response.status_code == 403


def test_signed_extra_role_claims_do_not_grant_access(workspace_request, auth_settings, tenants):
    users, workspaces = tenants
    now = int(datetime.now(timezone.utc).timestamp())
    key = bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value())
    token = jwt.encode({
        "sub": str(users["F"]), "iss": ISSUER, "aud": AUDIENCE, "token_type": "access",
        "iat": now, "nbf": now, "exp": now + 900,
        "workspace_id": str(workspaces["A"]), "role": "owner",
    }, key, algorithm="HS256")
    assert workspace_request(token=token).status_code == 404


def test_revocation_and_downgrade_apply_to_same_token(workspace_request, auth_settings, auth_database, tenants):
    users, workspaces = tenants
    token = issue_access_token(users["C"], auth_settings)
    assert workspace_request(permission=Permission.WORKSPACE_UPDATE, token=token).status_code == 200
    with auth_database.begin() as session:
        session.get(WorkspaceMember, (workspaces["A"], users["C"])).role = "viewer"
    assert workspace_request(permission=Permission.WORKSPACE_UPDATE, token=token).status_code == 403
    assert workspace_request(token=token).json()["role"] == "viewer"
    with auth_database.begin() as session:
        session.delete(session.get(WorkspaceMember, (workspaces["A"], users["C"])))
    assert workspace_request(token=token).status_code == 404
    assert workspace_request(workspace="B", token=token).status_code == 200


def test_ownership_overrides_redundant_membership(workspace_request, auth_database, tenants):
    users, workspaces = tenants
    with auth_database.begin() as session:
        session.add(WorkspaceMember(workspace_id=workspaces["A"], user_id=users["A"], role="viewer"))
    response = workspace_request(permission=Permission.WORKSPACE_DELETE)
    assert response.status_code == 200 and response.json()["role"] == "owner"


def test_disabled_owner_or_missing_identity_is_401_before_workspace_check(
    workspace_request, auth_database, auth_settings, tenants,
):
    users, _ = tenants
    token = issue_access_token(users["A"], auth_settings)
    assert workspace_request(token=token).status_code == 200
    with auth_database.begin() as session:
        session.get(User, users["A"]).is_active = False
    for response in (workspace_request(token=token), workspace_request(user=None, workspace=uuid4())):
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid access token"}
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("kind", ["database", "internal"])
def test_lookup_failures_do_not_become_permission_denials(workspace_request, auth_database, workspace_app, caplog, kind):
    diagnostic = "synthetic-workspace-query-details"

    @event.listens_for(auth_database.kw["bind"], "before_cursor_execute")
    def fail_workspace_query(connection, cursor, statement, parameters, context, executemany):
        if "FROM workspaces" in statement:
            if kind == "database":
                raise OperationalError(statement, {"diagnostic": diagnostic}, ConnectionError(diagnostic))
            raise RuntimeError(diagnostic)

    response = workspace_request("C")
    expected = (503, "Database is unavailable") if kind == "database" else (500, "Request failed")
    assert (response.status_code, response.json()["detail"]) == expected
    assert diagnostic not in response.text + caplog.text
    assert workspace_app.state.handler_calls == 0


@pytest.mark.parametrize(("user", "status"), [("A", 500), ("F", 404)])
def test_workspace_cleanup_blocks_success_but_preserves_denial(
    workspace_request, workspace_app, auth_database, monkeypatch, caplog, user, status,
):
    diagnostic = "synthetic-workspace-close-details"
    session = auth_database()
    close = Mock(side_effect=RuntimeError(diagnostic))
    invalidate = Mock(side_effect=RuntimeError(diagnostic))
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(session, "invalidate", invalidate)
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    try:
        response = workspace_request(user)
        assert response.status_code == status
        assert response.json() == {"detail": "Request failed" if status == 500 else "Workspace not found"}
        assert workspace_app.state.handler_calls == 0
        close.assert_called_once_with()
        invalidate.assert_called_once_with()
        assert diagnostic not in response.text + caplog.text
    finally:
        Session.close(session)


def test_caller_session_is_reused_without_flush_commit_or_implicit_loads(auth_database, tenants):
    users, workspaces = tenants
    engine = auth_database.kw["bind"]
    with Session(engine) as session:
        existing = session.get(User, users["C"])
        existing.email = "unflushed@example.com"
        queries = []
        commits = []

        @event.listens_for(engine, "before_cursor_execute")
        def record_sql(connection, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        @event.listens_for(engine, "commit")
        def record_commit(connection):
            commits.append(connection)

        access = require_workspace_permission(session, users["C"], workspaces["A"], Permission.WORKSPACE_UPDATE)
        assert access == WorkspaceAccessContext(workspaces["A"], users["A"], users["C"], WorkspaceRole.ADMIN)
        assert len(queries) == 3 and all(statement.lstrip().startswith("SELECT") for statement in queries)
        assert "password_hash" not in "".join(queries)
        assert not commits and existing in session.dirty and session.in_transaction()
        with pytest.raises(FrozenInstanceError):
            access.effective_role = WorkspaceRole.OWNER


def test_resolver_checks_active_account_before_workspace(auth_database, tenants):
    _, workspaces = tenants
    with auth_database() as session:
        with pytest.raises(InvalidAccessToken):
            resolve_workspace_access(session, uuid4(), workspaces["A"])
        with pytest.raises(WorkspaceNotFound):
            resolve_workspace_access(session, tenants[0]["A"], uuid4())


def test_probe_routes_are_absent_from_production_openapi():
    assert not any(path.startswith((PREFIX, "/auth-probe")) for path in app.openapi()["paths"])
