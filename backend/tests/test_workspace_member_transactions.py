"""Real SQLite member outcomes under faults; PostgreSQL locks compile offline."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api.dependencies import get_token_subject
from app.db import session as db
from app.main import app
from app.models import User, Workspace, WorkspaceMember
from app.services import workspace_members as service

MARKER = "synthetic-member-private-diagnostic"
UNKNOWN = {"detail": "Workspace commit outcome is unknown; reconcile before retrying"}


def snapshot(factory):
    with factory() as session:
        return (set(session.execute(select(WorkspaceMember.workspace_id, WorkspaceMember.user_id, WorkspaceMember.role, WorkspaceMember.joined_at))),
                set(session.execute(select(User.id, User.is_active))),
                set(session.execute(select(Workspace.id, Workspace.owner_id, Workspace.name, Workspace.updated_at))))


def mutate(api, tenants, method, **options):
    if method == "POST":
        return api(method, json={"user_id": str(tenants.users["F"]), "role": "viewer"}, **options)
    return api(method, target="E", **({"json": {"role": "editor"}} if method == "PATCH" else {}), **options)


def assert_applied(factory, tenants, method, before):
    after = snapshot(factory)
    assert after[1:] == before[1:]
    unaffected = lambda rows: {row for row in rows if not (row.workspace_id == tenants.spaces["A"] and row.user_id == tenants.users["E" if method != "POST" else "F"])}
    assert unaffected(after[0]) == unaffected(before[0])
    with factory() as check:
        row = check.get(WorkspaceMember, (tenants.spaces["A"], tenants.users["F" if method == "POST" else "E"]))
        if method == "DELETE":
            assert row is None
        else:
            assert row.role == ("viewer" if method == "POST" else "editor")


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("kind", ["integrity", "40001", "40P01", "runtime"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_after_commit_fault_reports_unknown_with_actual_member_write(
    member_api, workspace_database, workspace_tenants, monkeypatch, caplog, method, kind, cleanup_fails,
):
    before = snapshot(workspace_database)
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    observed = []
    sent = []
    def fail_after_commit(_session):
        observed.append(True)
        if kind == "integrity":
            raise IntegrityError(MARKER, {"private": MARKER}, RuntimeError(MARKER))
        if kind != "runtime":
            original = RuntimeError(MARKER)
            original.sqlstate = kind
            raise OperationalError(MARKER, {"private": MARKER}, original)
        raise RuntimeError(MARKER)
    async def observed_app(scope, receive, send):
        async def track(message):
            sent.append(message)
            await send(message)
        await app(scope, receive, track)
    event.listen(session, "after_commit", fail_after_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "get_session_factory", lambda: lambda: session)
            patch.setattr(session, "commit", commit)
            if cleanup_fails:
                for name in ("rollback", "close", "invalidate"):
                    patch.setattr(session, name, Mock(side_effect=RuntimeError(MARKER)))
            response = mutate(member_api, workspace_tenants, method, asgi_app=observed_app)
    finally:
        event.remove(session, "after_commit", fail_after_commit)
    assert_applied(workspace_database, workspace_tenants, method, before)
    assert observed == [True]
    commit.assert_called_once_with()
    assert response.status_code == 503 and response.json() == UNKNOWN
    assert response.headers["cache-control"] == "no-store"
    assert MARKER not in response.text + caplog.text
    assert "40001" not in response.text and "40P01" not in response.text
    assert sum(message["type"] == "http.response.start" for message in sent) == 1
    assert sum(message["type"] == "http.response.body" for message in sent) == 1


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("invalidate_fails", [False, True])
def test_confirmed_commit_survives_cleanup_failure(member_api, workspace_database, workspace_tenants, monkeypatch, method, invalidate_fails):
    before = snapshot(workspace_database)
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit)
        patch.setattr(session, "close", Mock(side_effect=RuntimeError(MARKER)))
        if invalidate_fails:
            patch.setattr(session, "invalidate", Mock(side_effect=RuntimeError(MARKER)))
        response = mutate(member_api, workspace_tenants, method)
    commit.assert_called_once_with()
    assert_applied(workspace_database, workspace_tenants, method, before)
    assert response.status_code == {"POST": 201, "PATCH": 200, "DELETE": 204}[method]


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("fault", ["flush", "write", "constraint", "commit"])
def test_failed_or_unknown_member_write_checks_persisted_state(member_api, workspace_database, workspace_tenants, monkeypatch, method, fault, caplog):
    before = snapshot(workspace_database)
    engine = workspace_database.kw["bind"]
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    def fail_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            raise OperationalError(MARKER, parameters, ConnectionError(MARKER))
    if fault == "constraint":
        operation = {"POST": "INSERT", "PATCH": "UPDATE", "DELETE": "DELETE"}[method]
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE TRIGGER member_reject BEFORE {operation} ON workspace_members BEGIN SELECT RAISE(ABORT, '{MARKER}'); END")
    if fault == "write":
        event.listen(engine, "before_cursor_execute", fail_write)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "get_session_factory", lambda: lambda: session)
            patch.setattr(session, "commit", commit)
            if fault == "flush":
                patch.setattr(session, "flush", Mock(side_effect=IntegrityError(MARKER, {}, RuntimeError(MARKER))))
            if fault == "commit":
                commit.side_effect = OperationalError(MARKER, {}, ConnectionError(MARKER))
            response = mutate(member_api, workspace_tenants, method)
    finally:
        if fault == "write":
            event.remove(engine, "before_cursor_execute", fail_write)
    assert snapshot(workspace_database) == before
    assert response.status_code == (503 if fault in ("write", "commit") else 500)
    assert commit.call_count == (1 if fault == "commit" else 0)
    assert (response.json() == UNKNOWN) == (fault == "commit")
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_dto_failure_cannot_commit(member_api, workspace_database, workspace_tenants, monkeypatch, method):
    before = snapshot(workspace_database)
    monkeypatch.setattr(service, "_public", Mock(side_effect=ValueError(MARKER)))
    assert mutate(member_api, workspace_tenants, method).status_code == 500
    assert snapshot(workspace_database) == before


def test_pk_rejection_handles_stale_duplicate_precheck(member_api, workspace_database, workspace_tenants, monkeypatch):
    with workspace_database.begin() as session:
        session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=workspace_tenants.users["F"], role="editor"))
    before = snapshot(workspace_database)
    # Simulate an obsolete absence observation. The real PK rejects the INSERT.
    monkeypatch.setattr(service, "_member", Mock(return_value=None))
    response = mutate(member_api, workspace_tenants, "POST")
    assert response.status_code == 409 and response.json() == {"detail": "Member already exists"}
    assert snapshot(workspace_database) == before


@pytest.mark.parametrize("state,constraint,expected", [("23505", "pk_workspace_members", True), ("23505", "other_constraint", False), ("23503", "pk_workspace_members", False), ("40001", "pk_workspace_members", False)])
def test_only_specific_postgresql_pk_rejection_maps_to_duplicate(state, constraint, expected):
    original = RuntimeError(MARKER)
    original.sqlstate = state
    original.diag = SimpleNamespace(constraint_name=constraint)
    assert service._duplicate_key(IntegrityError(MARKER, {}, original)) == expected


@pytest.mark.parametrize("change,status", [("actor-disabled", 401), ("actor-viewer", 403), ("actor-removed", 404), ("target-disabled", 404)])
def test_add_rechecks_database_after_token_dependency(member_api, workspace_database, workspace_tenants, monkeypatch, change, status):
    def verified():
        with workspace_database.begin() as session:
            if change in ("actor-disabled", "target-disabled"):
                session.get(User, workspace_tenants.users["C" if change == "actor-disabled" else "F"]).is_active = False
            else:
                member = session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["C"]))
                if change == "actor-removed":
                    session.delete(member)
                else:
                    member.role = "viewer"
        return workspace_tenants.users["C"]
    monkeypatch.setitem(app.dependency_overrides, get_token_subject, verified)
    response = mutate(member_api, workspace_tenants, "POST")
    assert response.status_code == status
    with workspace_database() as session:
        assert session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["F"])) is None


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("change,status", [("target-admin", 403), ("target-removed", 404)])
def test_target_current_role_is_reread_in_write_transaction(member_api, workspace_database, workspace_tenants, monkeypatch, method, change, status):
    def verified():
        with workspace_database.begin() as session:
            member = session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["E"]))
            if change == "target-admin":
                member.role = "admin"
            else:
                session.delete(member)
        return workspace_tenants.users["C"]
    monkeypatch.setitem(app.dependency_overrides, get_token_subject, verified)
    assert mutate(member_api, workspace_tenants, method).status_code == status
    with workspace_database() as session:
        member = session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["E"]))
        assert (member.role if member else None) == ("admin" if change == "target-admin" else None)


def test_denial_survives_all_session_cleanup_faults(member_api, workspace_database, workspace_tenants, monkeypatch):
    before = snapshot(workspace_database)
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit)
        for name in ("rollback", "close", "invalidate"):
            patch.setattr(session, name, Mock(side_effect=RuntimeError(MARKER)))
        response = member_api("PATCH", user="D", target="E", json={"role": "editor"})
    assert response.status_code == 403
    commit.assert_not_called()
    assert snapshot(workspace_database) == before


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_sorted_user_then_workspace_then_sorted_member_locks_compile_offline(member_api, workspace_database, workspace_tenants, method):
    target = "F" if method == "POST" else "E"
    ids = sorted([workspace_tenants.users["A"], workspace_tenants.users[target]])
    locks = []
    def capture(state):
        sql = str(state.statement.compile(dialect=postgresql.dialect()))
        if "FOR SHARE" in sql or "FOR UPDATE" in sql:
            locks.append(str(state.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
    event.listen(Session, "do_orm_execute", capture)
    try:
        assert mutate(member_api, workspace_tenants, method).status_code == {"POST": 201, "PATCH": 200, "DELETE": 204}[method]
    finally:
        event.remove(Session, "do_orm_execute", capture)
    assert len(locks) == 5
    for i, user_id in enumerate(ids):
        assert "FOR SHARE OF users" in locks[i] and str(user_id) in locks[i]
        assert "FOR UPDATE OF workspace_members" in locks[i+3] and str(user_id) in locks[i+3]
        assert str(workspace_tenants.spaces["A"]) in locks[i+3]
    assert "FOR UPDATE OF workspaces" in locks[2]


def test_preliminary_permission_cannot_replace_lock_time_authorization(member_api, workspace_database, workspace_tenants, monkeypatch):
    lock = service.lock_workspace_permission
    def changed_in_transaction(session, subject, workspace_id, permission, **options):
        # A persisted role change within this isolated transaction is seen by the
        # final check. This is not a PostgreSQL concurrent-revocation simulation.
        session.execute(WorkspaceMember.__table__.update().where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == subject,
        ).values(role="viewer"))
        return lock(session, subject, workspace_id, permission, **options)
    monkeypatch.setattr(service, "lock_workspace_permission", changed_in_transaction)
    response = member_api("POST", user="C", json={"user_id": str(workspace_tenants.users["F"]), "role": "viewer"})
    assert response.status_code == 403
    with workspace_database() as session:
        assert session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["F"])) is None


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_member_factory_outage_is_redacted_503(member_api, workspace_database, workspace_tenants, monkeypatch, caplog, method):
    before = snapshot(workspace_database)
    def unavailable():
        raise OperationalError(MARKER, {"private": MARKER}, ConnectionError(MARKER))
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", unavailable)
        response = member_api("GET") if method == "GET" else mutate(member_api, workspace_tenants, method)
    assert response.status_code == 503 and response.json() == {"detail": "Database is unavailable"}
    assert MARKER not in response.text + caplog.text
    assert snapshot(workspace_database) == before


def test_member_count_and_page_compile_postgresql_offline(member_api):
    compiled = []
    def capture(state):
        compiled.append(str(state.statement.compile(dialect=postgresql.dialect())))
    event.listen(Session, "do_orm_execute", capture)
    try:
        response = member_api("GET")
    finally:
        event.remove(Session, "do_orm_execute", capture)
    assert response.status_code == 200
    scoped = [sql for sql in compiled if "visible_workspace" in sql]
    assert len(scoped) == 2
    assert all("UNION ALL" in sql and "TIMESTAMP WITH TIME ZONE" in sql for sql in scoped)
    assert len(compiled) == 4  # Account, ownership, scoped count, scoped page.
