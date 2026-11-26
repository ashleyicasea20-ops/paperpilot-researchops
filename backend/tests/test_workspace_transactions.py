"""Real SQLite outcomes under injected write/commit/cleanup faults.

PostgreSQL locks and transaction options are inspected offline, never exercised
against PostgreSQL. The named in-memory fixture keeps data across invalidation.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.dependencies import get_token_subject
from app.db import session as db
from app.db.workspace_session import WorkspaceCommitUnknownError, workspace_write_session
from app.main import app
from app.models import User, Workspace, WorkspaceMember
from app.services import workspaces

MARKER = "synthetic-private-workspace-diagnostic"


def snapshot(factory):
    with factory() as session:
        return (
            set(session.execute(select(Workspace.id, Workspace.owner_id, Workspace.name, Workspace.created_at, Workspace.updated_at)).all()),
            set(session.execute(select(WorkspaceMember.workspace_id, WorkspaceMember.user_id, WorkspaceMember.role)).all()),
            set(session.execute(select(User.id, User.is_active)).all()),
        )


def mutate(api, method, user="A"):
    if method == "POST":
        return api(method, user=user, json={"name": "Transaction candidate"})
    return api(method, user=user, workspace="A", **({"json": {"name": "Transaction candidate"}} if method == "PATCH" else {}))


def assert_applied(factory, tenants, method):
    with factory() as session:
        rows = list(session.scalars(select(Workspace)))
        if method == "POST":
            assert len(rows) == 3 and any(row.name == "Transaction candidate" for row in rows)
        elif method == "PATCH":
            assert session.get(Workspace, tenants.spaces["A"]).name == "Transaction candidate"
        else:
            assert session.get(Workspace, tenants.spaces["A"]) is None
            assert list(session.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == tenants.spaces["A"]))) == []
        assert session.get(Workspace, tenants.spaces["B"]).name == "Workspace B"
        assert len(list(session.scalars(select(User)))) == 6


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("failure", ["integrity", "40001", "40P01", "unexpected"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_after_commit_fault_preserves_durable_data_and_reports_unknown(
    workspace_api, workspace_database, workspace_tenants, monkeypatch, caplog,
    method, failure, cleanup_fails,
):
    session = workspace_database()
    if failure == "integrity":
        error = IntegrityError("synthetic after_commit SQL", {"private": MARKER}, RuntimeError(MARKER))
    elif failure in ("40001", "40P01"):
        original = RuntimeError(MARKER)
        original.sqlstate = failure
        error = OperationalError("synthetic after_commit SQL", {"private": MARKER}, original)
    else:
        error = RuntimeError(MARKER)
    phases = []
    propagated = []
    sent = []

    def fail_after_commit(_session):
        phases.append(("after_commit", type(error)))
        raise error

    async def observed_app(scope, receive, send):
        async def observe(message):
            sent.append(message)
            await send(message)
        await app(scope, receive, observe)

    real_commit = session.commit

    def observed_commit():
        try:
            real_commit()
        except Exception as raised:
            propagated.append((type(raised), raised is error))
            raise

    commit = Mock(side_effect=observed_commit)
    event.listen(session, "after_commit", fail_after_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "get_session_factory", lambda: lambda: session)
            patch.setattr(session, "commit", commit)
            if cleanup_fails:
                patch.setattr(session, "rollback", Mock(side_effect=RuntimeError(MARKER)))
                patch.setattr(session, "close", Mock(side_effect=RuntimeError(MARKER)))
            response = mutate(lambda method, **options: workspace_api(method, asgi_app=observed_app, **options), method)
    finally:
        event.remove(session, "after_commit", fail_after_commit)

    # A separate Session observes the real commit even though commit() raised.
    assert_applied(workspace_database, workspace_tenants, method)
    assert phases == [("after_commit", type(error))]
    assert propagated == [(type(error), True)]
    commit.assert_called_once_with()
    assert response.status_code == 503
    assert response.json() == {"detail": "Workspace commit outcome is unknown; reconcile before retrying"}
    assert response.headers["cache-control"] == "no-store"
    assert MARKER not in response.text + caplog.text
    assert "synthetic after_commit SQL" not in response.text + caplog.text
    assert "40001" not in response.text and "40P01" not in response.text
    assert sum(message["type"] == "http.response.start" for message in sent) == 1
    assert sum(message["type"] == "http.response.body" for message in sent) == 1
    assert workspace_database.kw["bind"].pool.checkedout() == 0


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_real_database_rejection_before_commit_preserves_state(
    workspace_api, workspace_database, monkeypatch, caplog, method, cleanup_fails,
):
    before = snapshot(workspace_database)
    operation = {"POST": "INSERT", "PATCH": "UPDATE", "DELETE": "DELETE"}[method]
    engine = workspace_database.kw["bind"]
    with engine.begin() as connection:
        # Only this isolated memory database: the database itself rejects DML.
        connection.exec_driver_sql(
            f"CREATE TRIGGER reject_workspace_write BEFORE {operation} ON workspaces "
            f"BEGIN SELECT RAISE(ABORT, '{MARKER}'); END"
        )
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    rejections = []

    def observe_rejection(context):
        rejections.append((type(context.sqlalchemy_exception), context.original_exception.sqlite_errorname))

    event.listen(engine, "handle_error", observe_rejection)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "get_session_factory", lambda: lambda: session)
            patch.setattr(session, "commit", commit)
            if cleanup_fails:
                patch.setattr(session, "rollback", Mock(side_effect=RuntimeError(MARKER)))
                patch.setattr(session, "close", Mock(side_effect=RuntimeError(MARKER)))
            response = mutate(workspace_api, method)
    finally:
        event.remove(engine, "handle_error", observe_rejection)
    assert snapshot(workspace_database) == before
    assert rejections == [(IntegrityError, "SQLITE_CONSTRAINT_TRIGGER")]
    commit.assert_not_called()
    assert response.status_code == 500
    assert response.json() == {"detail": "Workspace request failed"}
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_flush_failure_before_commit_cannot_report_success(
    workspace_api, workspace_database, monkeypatch, method,
):
    before = snapshot(workspace_database)
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit)
        patch.setattr(session, "flush", Mock(side_effect=IntegrityError(None, {}, RuntimeError(MARKER))))
        response = mutate(workspace_api, method)
    assert snapshot(workspace_database) == before
    commit.assert_not_called()
    assert response.status_code == 500
    assert "unknown" not in response.text


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_commit_callback_flush_rejection_has_no_proven_commit_outcome(
    workspace_api, workspace_database, workspace_tenants, monkeypatch, method,
):
    before = snapshot(workspace_database)
    session = workspace_database()

    def add_invalid_member(_session):
        # commit() performs another flush after before_commit callbacks.
        session.add(WorkspaceMember(workspace_id=uuid4(), user_id=workspace_tenants.users["A"], role="viewer"))

    commit = Mock(wraps=session.commit)
    event.listen(session, "before_commit", add_invalid_member)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "get_session_factory", lambda: lambda: session)
            patch.setattr(session, "commit", commit)
            response = mutate(workspace_api, method)
    finally:
        event.remove(session, "before_commit", add_invalid_member)
    assert snapshot(workspace_database) == before
    commit.assert_called_once_with()
    assert response.status_code == 503
    assert response.json() == {"detail": "Workspace commit outcome is unknown; reconcile before retrying"}


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("database_error", [False, True])
def test_write_failure_rolls_back_real_data(workspace_api, workspace_database, method, database_error, caplog):
    before = snapshot(workspace_database)
    engine = workspace_database.kw["bind"]

    def fail_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            if database_error:
                raise OperationalError(statement, parameters, ConnectionError(MARKER))
            raise RuntimeError(MARKER)

    event.listen(engine, "before_cursor_execute", fail_write)
    try:
        response = mutate(workspace_api, method)
    finally:
        event.remove(engine, "before_cursor_execute", fail_write)
    assert response.status_code == (503 if database_error else 500)
    assert snapshot(workspace_database) == before
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("failure", ["unproven-integrity", "unknown-before", "unknown-after"])
def test_commit_faults_report_unknown_and_preserve_actual_outcome(
    workspace_api, workspace_database, workspace_tenants, monkeypatch, caplog, method, failure,
):
    before = snapshot(workspace_database)
    session = workspace_database()
    real_commit = session.commit

    def commit():
        if failure == "unproven-integrity":
            raise IntegrityError("synthetic deferred constraint", {}, RuntimeError(MARKER))
        if failure == "unknown-after":
            real_commit()
        raise OperationalError(None, {}, ConnectionError(MARKER))

    commit_mock = Mock(side_effect=commit)
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit_mock)
        response = mutate(workspace_api, method)
    assert commit_mock.call_count == 1
    assert response.status_code == 503
    assert response.json() == {"detail": "Workspace commit outcome is unknown; reconcile before retrying"}
    if failure == "unknown-after":
        assert_applied(workspace_database, workspace_tenants, method)
    else:
        assert snapshot(workspace_database) == before
    assert MARKER not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
def test_commit_sqlstate_alone_does_not_prove_rejection_or_allow_retry(workspace_api, workspace_database, monkeypatch, method, sqlstate):
    before = snapshot(workspace_database)
    session = workspace_database()
    original = RuntimeError(MARKER)
    original.sqlstate = sqlstate
    commit = Mock(side_effect=OperationalError(None, {}, original))
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit)
        response = mutate(workspace_api, method)
    assert response.status_code == 503
    assert response.json() == {"detail": "Workspace commit outcome is unknown; reconcile before retrying"}
    assert commit.call_count == 1
    assert snapshot(workspace_database) == before


@pytest.mark.parametrize("method,status", [("POST", 201), ("PATCH", 200), ("DELETE", 204)])
@pytest.mark.parametrize("invalidation_fails", [False, True])
def test_acknowledged_commit_survives_cleanup_faults(
    workspace_api, workspace_database, workspace_tenants, monkeypatch, method, status, invalidation_fails, caplog,
):
    session = workspace_database()
    close = Mock(side_effect=RuntimeError(MARKER))
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "close", close)
        if invalidation_fails:
            patch.setattr(session, "invalidate", Mock(side_effect=RuntimeError(MARKER)))
        response = mutate(workspace_api, method)
    assert response.status_code == status
    assert close.call_count == 1
    assert_applied(workspace_database, workspace_tenants, method)
    assert workspace_database.kw["bind"].pool.checkedout() == 0
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize("user,workspace,status", [("D", "A", 403), ("F", "A", 404), ("A", "missing", 404)])
def test_primary_denial_survives_rollback_close_and_invalidation_faults(
    workspace_api, workspace_database, monkeypatch, user, workspace, status, caplog,
):
    before = snapshot(workspace_database)
    session = workspace_database()
    commit = Mock(wraps=session.commit)
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit)
        patch.setattr(session, "rollback", Mock(side_effect=RuntimeError(MARKER)))
        patch.setattr(session, "close", Mock(side_effect=RuntimeError(MARKER)))
        patch.setattr(session, "invalidate", Mock(side_effect=RuntimeError(MARKER)))
        response = workspace_api("PATCH", user=user, workspace=uuid4() if workspace == "missing" else workspace, json={"name": "Denied"})
    assert response.status_code == status
    commit.assert_not_called()
    assert snapshot(workspace_database) == before
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_rollback_failure_does_not_commit_failed_writes(workspace_api, workspace_database, monkeypatch, method):
    before = snapshot(workspace_database)
    session = workspace_database()
    commit = Mock(side_effect=IntegrityError(None, {}, RuntimeError(MARKER)))
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "commit", commit)
        patch.setattr(session, "rollback", Mock(side_effect=RuntimeError(MARKER)))
        response = mutate(workspace_api, method)
    commit.assert_called_once_with()
    assert response.status_code == 503
    assert response.json() == {"detail": "Workspace commit outcome is unknown; reconcile before retrying"}
    assert snapshot(workspace_database) == before


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "GET"])
def test_factory_outage_is_safe_503(workspace_api, workspace_database, monkeypatch, method, caplog):
    before = snapshot(workspace_database)

    def unavailable():
        raise OperationalError(None, {"private": MARKER}, ConnectionError(MARKER))

    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: unavailable)
        response = mutate(workspace_api, method) if method != "GET" else workspace_api("GET")
    assert response.status_code == 503
    assert snapshot(workspace_database) == before
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_response_preparation_failure_cannot_commit(workspace_api, workspace_database, monkeypatch, method):
    before = snapshot(workspace_database)
    with monkeypatch.context() as patch:
        patch.setattr(workspaces, "_public", Mock(side_effect=ValueError(MARKER)))
        response = mutate(workspace_api, method)
    assert response.status_code == 500
    assert snapshot(workspace_database) == before


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_writes_recheck_account_after_token_dependency(workspace_api, workspace_database, workspace_tenants, monkeypatch, method):
    def verified_but_now_disabled():
        with workspace_database.begin() as session:
            session.get(User, workspace_tenants.users["A"]).is_active = False
        return workspace_tenants.users["A"]

    with monkeypatch.context() as patch:
        patch.setitem(app.dependency_overrides, get_token_subject, verified_but_now_disabled)
        response = mutate(workspace_api, method)
    assert response.status_code == 401
    with workspace_database() as session:
        assert len(list(session.scalars(select(Workspace)))) == 2
        assert session.get(Workspace, workspace_tenants.spaces["A"]).name == "Workspace A"


@pytest.mark.parametrize("change,status", [("removed", 404), ("viewer", 403)])
def test_write_rechecks_membership_after_token_dependency(workspace_api, workspace_database, workspace_tenants, monkeypatch, change, status):
    def changed_member():
        with workspace_database.begin() as session:
            member = session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["C"]))
            if change == "removed":
                session.delete(member)
            else:
                member.role = "viewer"
        return workspace_tenants.users["C"]

    with monkeypatch.context() as patch:
        patch.setitem(app.dependency_overrides, get_token_subject, changed_member)
        response = mutate(workspace_api, "PATCH", user="C")
    assert response.status_code == status
    with workspace_database() as session:
        assert session.get(Workspace, workspace_tenants.spaces["A"]).name == "Workspace A"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_old_owner_is_not_authorized_after_ownership_change(workspace_api, workspace_database, workspace_tenants, monkeypatch, method):
    def changed_owner():
        with workspace_database.begin() as session:
            session.get(Workspace, workspace_tenants.spaces["A"]).owner_id = workspace_tenants.users["B"]
        return workspace_tenants.users["A"]

    with monkeypatch.context() as patch:
        patch.setitem(app.dependency_overrides, get_token_subject, changed_owner)
        response = mutate(workspace_api, method)
    assert response.status_code == 404
    with workspace_database() as session:
        workspace = session.get(Workspace, workspace_tenants.spaces["A"])
        assert workspace.owner_id == workspace_tenants.users["B"] and workspace.name == "Workspace A"


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_connection_close_fallback_after_invalidation_fault(workspace_api, workspace_database, workspace_tenants, monkeypatch, method):
    session = workspace_database()
    real_connection = session.connection
    connection_closed = []

    def connection(*args, **kwargs):
        lease = real_connection(*args, **kwargs)
        actual_close = lease.close

        def close():
            connection_closed.append(True)
            actual_close()

        monkeypatch.setattr(lease, "invalidate", Mock(side_effect=RuntimeError(MARKER)))
        monkeypatch.setattr(lease, "close", close)
        return lease

    with monkeypatch.context() as patch:
        patch.setattr(db, "get_session_factory", lambda: lambda: session)
        patch.setattr(session, "connection", connection)
        patch.setattr(session, "close", Mock(side_effect=RuntimeError(MARKER)))
        patch.setattr(session, "invalidate", Mock(side_effect=RuntimeError(MARKER)))
        response = mutate(workspace_api, method)
    assert response.status_code == {"POST": 201, "PATCH": 200, "DELETE": 204}[method]
    assert connection_closed
    assert_applied(workspace_database, workspace_tenants, method)


def test_unknown_commit_error_discards_sensitive_exception_context(workspace_database, monkeypatch):
    session = workspace_database()
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(session, "commit", Mock(side_effect=OperationalError(None, {"secret": MARKER}, RuntimeError(MARKER))))
    with pytest.raises(WorkspaceCommitUnknownError) as caught:
        with workspace_write_session():
            pass
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert MARKER not in str(caught.value)


def test_write_lock_order_and_postgresql_sql_compile_offline(workspace_api, workspace_database):
    statements = []

    def capture(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            statements.append(str(state.statement.compile(dialect=postgresql.dialect())))

    from sqlalchemy.orm import Session
    event.listen(Session, "do_orm_execute", capture)
    try:
        assert mutate(workspace_api, "PATCH", user="C").status_code == 200
    finally:
        event.remove(Session, "do_orm_execute", capture)
    assert len(statements) == 3
    assert "FOR SHARE OF users" in statements[0]
    assert "FOR UPDATE OF workspaces" in statements[1]
    assert "FOR UPDATE OF workspace_members" in statements[2]


def test_postgresql_transaction_options_are_bounded_without_connecting(monkeypatch):
    connection = Mock()
    session = Mock()
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session.connection.return_value = connection
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    with workspace_write_session() as current:
        assert current is session
    session.connection.assert_called_once_with(execution_options={"isolation_level": "READ COMMITTED"})
    sql = [str(call.args[0]) for call in session.execute.call_args_list]
    assert sql == ["SET LOCAL lock_timeout = '5s'", "SET LOCAL statement_timeout = '15s'"]
    session.commit.assert_called_once_with()


def test_commit_unknown_sends_only_one_response(workspace_database, workspace_tenants, monkeypatch):
    session = workspace_database()
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(session, "commit", Mock(side_effect=OperationalError(None, {}, ConnectionError(MARKER))))
    import json
    body = json.dumps({"name": "One response"}).encode()
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/v1/workspaces", "raw_path": b"/api/v1/workspaces", "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"authorization", ("Bearer " + workspace_tenants.tokens["A"]).encode())],
        "client": ("test", 1), "server": ("test", 80),
    }
    asyncio.run(app(scope, receive, send))
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 503
    assert sum(message["type"] == "http.response.body" for message in sent) == 1
