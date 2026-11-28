"""G3: allow the current nested rollback without opening a SQL text bypass."""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.postgres import policy
from tests.postgres.errors import PgSafetyError


def test_nested_and_root_rollback_survive_fuse(guard_lab):
    with guard_lab() as case:
        with case.engine.connect() as connection:
            nested = connection.begin_nested()
            policy.trip(case.engine)
            before = len(case.lab.sql)
            for sql in ("SELECT 1", "UPDATE demo SET value=1"):
                with pytest.raises(PgSafetyError): connection.exec_driver_sql(sql)
            nested.rollback()
            assert len(case.lab.sql) == before + 1
            assert case.lab.sql[-1] == "ROLLBACK TO SAVEPOINT sa_savepoint_1"
            connection.rollback()
        assert case.lab.commits == 0


def test_session_savepoint_rollback_survives_fuse(guard_lab):
    with guard_lab() as case:
        with Session(case.engine) as session:
            nested = session.begin_nested()
            session.execute(text("SELECT 1"))
            policy.trip(case.engine)
            nested.rollback()
            session.rollback()
        assert case.lab.commits == 0


@pytest.mark.parametrize("sql", ["ROLLBACK TO SAVEPOINT sa_savepoint_1",
    "ROLLBACK TO SAVEPOINT sa_savepoint_1; DELETE FROM demo", "ROLLBACK /* fake */ TO SAVEPOINT sa_savepoint_1",
    "ROLLBACK TO SAVEPOINT other", "RELEASE SAVEPOINT sa_savepoint_1", "SAVEPOINT other", "COMMIT", "CREATE TABLE demo (x int)"])
def test_direct_cleanup_text_does_not_borrow_exception(guard_lab, sql):
    with guard_lab() as case:
        with case.engine.connect() as connection:
            nested = connection.begin_nested()
            policy.trip(case.engine)
            before = len(case.lab.sql)
            with pytest.raises(PgSafetyError): connection.exec_driver_sql(sql)
            assert len(case.lab.sql) == before
            connection.rollback()


def test_direct_dialect_rollback_cannot_forge_current_savepoint(guard_lab):
    with guard_lab() as case:
        with case.engine.connect() as connection:
            connection.begin_nested()
            policy.trip(case.engine)
            before = len(case.lab.sql)
            with pytest.raises(PgSafetyError):
                case.engine.dialect.do_rollback_to_savepoint(connection, "sa_savepoint_1")
            assert len(case.lab.sql) == before
            connection.rollback()


def test_savepoint_cleanup_error_does_not_leave_a_permission(guard_lab, monkeypatch, caplog):
    from tests.postgres._guard_cases import Cursor
    with guard_lab() as case:
        with case.engine.connect() as connection:
            nested = connection.begin_nested()
            policy.trip(case.engine)
            def fail(*a, **kw): raise RuntimeError("synthetic-cleanup-private-marker")
            with monkeypatch.context() as patch:
                patch.setattr(Cursor, "execute", fail)
                with pytest.raises(PgSafetyError) as caught: nested.rollback()
            assert "synthetic-cleanup-private-marker" not in str(caught.value)
            assert caught.value.__context__ is None
            with pytest.raises(PgSafetyError): connection.exec_driver_sql("SELECT 1")
            connection.rollback()
        assert "synthetic-cleanup-private-marker" not in caplog.text
        assert case.lab.commits == 0


def test_forged_cleanup_capability_is_rejected(guard_lab):
    from tests.postgres import cleanup
    from sqlalchemy.sql.elements import RollbackToSavepointClause
    with guard_lab() as case:
        with case.engine.connect() as connection:
            nested = connection.begin_nested()
            driver = connection.connection.driver_connection
            fake = cleanup._Cleanup(nested, connection, driver, "sa_savepoint_1")
            policy.trip(case.engine)
            token = cleanup._savepoint.set(fake)
            try:
                with pytest.raises(PgSafetyError): connection.execute(RollbackToSavepointClause("sa_savepoint_1"))
                with pytest.raises(PgSafetyError): driver.cursor().execute("ROLLBACK TO SAVEPOINT sa_savepoint_1")
            finally: cleanup._savepoint.reset(token)
            connection.rollback()


@pytest.mark.parametrize("ending", ["close", "invalidate"])
def test_dead_savepoint_does_not_reconnect(guard_lab, ending):
    with guard_lab() as case:
        connection = case.engine.connect()
        nested = connection.begin_nested()
        getattr(connection, ending)()
        before = case.lab.created, len(case.lab.sql)
        try:
            if nested.is_active:
                with pytest.raises(PgSafetyError): nested.rollback()
            else:
                assert not nested.is_active
            assert (case.lab.created, len(case.lab.sql)) == before
        finally: connection.close()


def test_nested_close_and_session_close_release_fused_connections(guard_lab):
    with guard_lab() as case:
        session = Session(case.engine)
        session.begin_nested()
        session.execute(text("SELECT 1"))
        policy.trip(case.engine)
        session.close()
        assert any(sql.startswith("ROLLBACK TO SAVEPOINT") for sql in case.lab.sql)
        assert case.lab.commits == 0


def test_cleanup_capability_cannot_cross_engines(guard_lab):
    from contextvars import copy_context
    from sqlalchemy import event
    with guard_lab() as a, guard_lab() as b:
        with a.engine.connect() as ca, b.engine.connect() as cb:
            na, nb = ca.begin_nested(), cb.begin_nested()
            policy.trip(a.engine)
            policy.trip(b.engine)
            before = len(b.lab.sql)
            captured = []
            @event.listens_for(a.engine, "before_cursor_execute")
            def try_other(connection, cursor, statement, parameters, context, many):
                captured.append(copy_context())
                with pytest.raises(PgSafetyError): cb.exec_driver_sql("ROLLBACK TO SAVEPOINT sa_savepoint_1")
            na.rollback()
            assert len(b.lab.sql) == before
            with pytest.raises(PgSafetyError):
                captured[0].run(ca.exec_driver_sql, "ROLLBACK TO SAVEPOINT sa_savepoint_1")
            nb.rollback()
            ca.rollback()
            cb.rollback()


def test_live_cleanup_scope_cannot_authorize_extra_operations(guard_lab):
    from sqlalchemy import event
    from sqlalchemy.sql.elements import RollbackToSavepointClause
    with guard_lab() as case:
        with case.engine.connect() as connection:
            nested = connection.begin_nested()
            driver = connection.connection.driver_connection
            policy.trip(case.engine)
            before = len(case.lab.sql)
            @event.listens_for(case.engine, "before_cursor_execute")
            def attempt_extra(conn, cursor, statement, parameters, context, many):
                with pytest.raises(PgSafetyError): conn.exec_driver_sql(statement)
                with pytest.raises(PgSafetyError): conn.execute(RollbackToSavepointClause("other"))
                with pytest.raises(PgSafetyError): driver.cursor().executemany(statement, [()])
                with pytest.raises(PgSafetyError): driver.commit()
            nested.rollback()
            assert len(case.lab.sql) == before + 1
            connection.rollback()
