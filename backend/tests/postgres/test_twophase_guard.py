"""G2: 2PC is unsupported, including raw and direct dialect entry points."""
import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from tests.postgres import policy
from tests.postgres.errors import PgSafetyError


@pytest.mark.parametrize("entry", ["connection", "session", "dialect_begin", "dialect_prepare", "dialect_commit",
                                   "dbapi_begin", "dbapi_prepare", "dbapi_commit"])
def test_twophase_is_denied_even_with_a_verified_lease(guard_lab, entry, caplog):
    with guard_lab(psycopg_dialect=True) as case:
        with case.engine.connect() as connection:
            driver = connection.connection.driver_connection
            with pytest.raises(PgSafetyError) as caught:
                if entry == "connection": connection.begin_twophase("synthetic-xid")
                elif entry == "session":
                    with Session(case.engine, twophase=True) as session: session.connection()
                elif entry.startswith("dialect_"):
                    operation = getattr(case.engine.dialect, "do_" + entry.removeprefix("dialect_") + "_twophase")
                    operation(connection, "synthetic-xid")
                elif entry == "dbapi_begin": driver.tpc_begin("synthetic-xid")
                elif entry == "dbapi_prepare": driver.tpc_prepare()
                else: driver.tpc_commit()
            assert "synthetic-xid" not in str(caught.value) + caplog.text
            assert (case.lab.tpc_begins, case.lab.tpc_prepares, case.lab.tpc_commits) == (0, 0, 0)


@pytest.mark.parametrize("prepared", [False, True])
def test_preexisting_twophase_cannot_commit_after_fuse(guard_lab, monkeypatch, prepared):
    with guard_lab(psycopg_dialect=True) as case:
        with case.engine.connect() as connection:
            # Simulate an inherited SQLAlchemy transaction, without performing
            # a real or simulated DBAPI BEGIN/PREPARE. Restore before testing.
            with monkeypatch.context() as setup:
                setup.setattr(Connection, "_begin_twophase_impl", lambda *a: None)
                transaction = connection.begin_twophase("synthetic-existing-xid")
            transaction._is_prepared = prepared
            policy.trip(case.engine)
            with pytest.raises(PgSafetyError): transaction.commit()
            assert case.lab.tpc_commits == 0 and case.lab.tpc_rollbacks == 1
            assert connection.get_transaction() is None
            connection.rollback()


def test_preexisting_twophase_rollback_remains_bounded(guard_lab, monkeypatch):
    with guard_lab(psycopg_dialect=True) as case:
        with case.engine.connect() as connection:
            with monkeypatch.context() as setup:
                setup.setattr(Connection, "_begin_twophase_impl", lambda *a: None)
                transaction = connection.begin_twophase("synthetic-existing-xid")
            policy.trip(case.engine)
            transaction.rollback()
            assert case.lab.tpc_rollbacks == 1 and case.lab.tpc_commits == 0


@pytest.mark.parametrize("operation", ["begin", "prepare", "commit"])
def test_unbound_psycopg_dialect_cannot_escape_dbapi_gate(guard_lab, operation):
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
    with guard_lab(psycopg_dialect=True) as case:
        with case.engine.connect() as connection:
            method = getattr(PGDialect_psycopg, "do_" + operation + "_twophase")
            with pytest.raises(PgSafetyError): method(case.engine.dialect, connection, "synthetic-xid")
            assert (case.lab.tpc_begins, case.lab.tpc_prepares, case.lab.tpc_commits) == (0, 0, 0)


def test_legacy_twophase_session_commit_is_aborted(guard_lab, monkeypatch):
    with guard_lab(psycopg_dialect=True) as case:
        session = Session(case.engine, twophase=True)
        with monkeypatch.context() as setup:
            setup.setattr(Connection, "_begin_twophase_impl", lambda *a: None)
            session.connection()
        policy.trip(case.engine)
        try:
            with pytest.raises(PgSafetyError): session.commit()
            session.rollback()
        finally: session.close()
        assert case.lab.tpc_commits == 0 and case.lab.tpc_rollbacks == 1


def test_legacy_rollback_failure_is_redacted_and_released(guard_lab, monkeypatch, caplog):
    from tests.postgres._synthetic import DBAPI
    with guard_lab(psycopg_dialect=True) as case:
        with case.engine.connect() as connection:
            with monkeypatch.context() as setup:
                setup.setattr(Connection, "_begin_twophase_impl", lambda *a: None)
                transaction = connection.begin_twophase("synthetic-xid")
            def fail(*a, **kw): raise RuntimeError("synthetic-tpc-private-marker")
            monkeypatch.setattr(DBAPI, "tpc_rollback", fail)
            with pytest.raises(PgSafetyError) as caught: transaction.rollback()
            assert "synthetic-tpc-private-marker" not in str(caught.value) + caplog.text
            assert caught.value.__context__ is None
        assert case.lab.tpc_commits == 0
