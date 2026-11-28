"""Spies prove refusals happen before any underlying driver call."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest
from sqlalchemy import create_engine

from tests.postgres import engine as engines, guards, safety
from tests.postgres.settings import PgOptIn, PgSafetyError


@pytest.mark.parametrize("cli,enable", [(False, None), (False, "1"), (True, None),
                                       (True, "0"), (True, "true"), (1, "1"), ("false", "1")])
def test_two_independent_opt_ins(settings, monkeypatch, cli, enable):
    driver = Mock()
    monkeypatch.setattr(engines, "open_driver_connection", driver)
    with pytest.raises(PgSafetyError):
        engines.create_test_engine(settings, PgOptIn(pg_test=cli, enable=enable))
    driver.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("host", "localhost"), ("host", "127.0.0.1,localhost"), ("host", ""),
    ("host", "/var/run/postgresql"), ("port", 5432), ("db_name", "paperpilot_dev"),
    ("db_name", "paperpilot_migration_test"), ("db_name", "postgres"),
    ("db_name", "template0"), ("db_name", "template1"), ("user", "postgres"),
    ("user", "paperpilot_app"), ("environment_id", ""),
])
def test_bad_targets_make_zero_driver_calls(settings, opt_in, monkeypatch, field, value):
    driver = Mock()
    monkeypatch.setattr(engines, "open_driver_connection", driver)
    with pytest.raises(PgSafetyError):
        engines.create_test_engine(settings.model_copy(update={field: value}), opt_in)
    driver.assert_not_called()


@pytest.mark.parametrize("entry", ["module", "sync", "async", "pq", "pq-start", "sqlalchemy", "generator", "base-generator"])
def test_direct_driver_entry_points_are_blocked(monkeypatch, entry):
    driver = Mock(side_effect=AssertionError("Underlying driver must never be reached"))
    monkeypatch.setattr(guards, "_driver_connect", driver)
    monkeypatch.setattr(guards, "_driver_pgconn", SimpleNamespace(connect=driver, connect_start=driver))
    monkeypatch.setattr(guards, "_driver_generator", driver)
    engine = None
    try:
        with pytest.raises(PgSafetyError):
            if entry == "module":
                psycopg.connect("")
            elif entry == "sync":
                psycopg.Connection.connect("")
            elif entry == "async":
                asyncio.run(psycopg.AsyncConnection.connect(""))
            elif entry == "pq":
                psycopg.pq.PGconn.connect(b"")
            elif entry == "pq-start":
                psycopg.pq.PGconn.connect_start(b"")
            elif entry == "generator":
                psycopg.generators.connect("")
            elif entry == "base-generator":
                next(psycopg.Connection._connect_gen(""))
            else:
                engine = create_engine("postgresql+psycopg://", hide_parameters=True)
                engine.connect()
    finally:
        if engine is not None:
            engine.dispose()
    driver.assert_not_called()


@pytest.mark.parametrize("name", ["get_engine", "get_session_factory"])
def test_application_engine_factory_is_forbidden(name):
    from app.db import session
    with pytest.raises(PgSafetyError):
        getattr(session, name)()


def test_real_environment_open_audit_is_rejected_without_opening_it():
    # Emit the audit event directly: never open or read the real file.
    with pytest.raises(PermissionError):
        sys.audit("open", guards._real_env, "r", 0)


def test_driver_failure_is_redacted(settings, opt_in, monkeypatch):
    marker = "synthetic-connection-private-marker"
    driver = Mock(side_effect=RuntimeError(marker))
    monkeypatch.setattr(guards, "_driver_connect", driver)
    with pytest.raises(PgSafetyError) as caught:
        guards._call_redacted(driver)
    assert marker not in str(caught.value)
    assert caught.value.__context__ is None
    assert guards._allowed.get() is None
    driver.assert_called_once()
    # A temporary authorized scope must not allow a nested, different target.
    token = guards._allowed.set({"host": "127.0.0.1", "port": 55432})
    try:
        with pytest.raises(PgSafetyError):
            psycopg.Connection.connect(host="127.0.0.1", port=5432)
        with pytest.raises(PgSafetyError):
            psycopg.pq.PGconn.connect_start(b"host=127.0.0.1 port=5432")
    finally:
        guards._allowed.reset(token)
    driver.assert_called_once()


def test_lazy_engine_and_attestation_gate(settings, opt_in, snapshot, mock_connection, monkeypatch):
    driver = Mock(side_effect=AssertionError("No physical connections in offline tests"))
    monkeypatch.setattr(engines, "open_driver_connection", driver)
    monkeypatch.setattr(safety, "read_snapshot", Mock(return_value=snapshot))
    engine = engines.create_offline_test_engine(settings, opt_in)
    try:
        with pytest.raises(PgSafetyError):
            engines.verified_session_factory(engine)
        with pytest.raises(PgSafetyError):
            engine.dispatch.before_cursor_execute(mock_connection, None, "DELETE FROM users", (), None, False)
        engine.dispatch.engine_connect(mock_connection)
        factory = engines.verified_session_factory(engine)
        with factory() as session:
            assert session.bind is engine
        engine.dispatch.before_cursor_execute(mock_connection, None, "SELECT 1", (), None, False)
        # Re-attestation for Alembic must work with the Engine SQL guard active.
        from tests.postgres.migrations import prepare_test_migration
        def guarded_statement(sql):
            engine.dispatch.before_cursor_execute(mock_connection, None, sql, (), None, False)
        mock_connection.exec_driver_sql.side_effect = guarded_statement
        prepare_test_migration(mock_connection, settings, opt_in)
        engine.pool.dispatch.checkin(None, SimpleNamespace(info=mock_connection.info))
        with pytest.raises(PgSafetyError):
            engine.dispatch.before_cursor_execute(mock_connection, None, "DELETE FROM users", (), None, False)
    finally:
        engine.dispose()
    driver.assert_not_called()


def test_failed_attestation_cannot_start_dml(settings, opt_in, mock_connection, monkeypatch):
    monkeypatch.setattr(safety, "read_snapshot", Mock(side_effect=LookupError("missing environment marker")))
    driver = Mock()
    monkeypatch.setattr(engines, "open_driver_connection", driver)
    engine = engines.create_offline_test_engine(settings, opt_in)
    dml = Mock()
    try:
        with pytest.raises(PgSafetyError):
            engine.dispatch.engine_connect(mock_connection)
        with pytest.raises(PgSafetyError):
            engine.dispatch.before_cursor_execute(mock_connection, None, "INSERT INTO users", (), None, False)
            dml()
        with pytest.raises(PgSafetyError):
            engines.verified_session_factory(engine)
    finally:
        engine.dispose()
    dml.assert_not_called()
    driver.assert_not_called()


def test_independent_auth_key_does_not_use_application_environment(monkeypatch):
    from app.core.token import issue_access_token, read_access_token
    from uuid import uuid4
    monkeypatch.setenv("JWT_SECRET_KEY", "unusable-application-key-sentinel")
    settings = engines.synthetic_auth_settings()
    subject = uuid4()
    assert read_access_token(issue_access_token(subject, settings), settings) == subject


@pytest.mark.parametrize("gate", [
    PgOptIn(pg_fault=True, fault_enable="1"),
    PgOptIn(pg_test=True, enable="1"),
    PgOptIn(pg_test=True, pg_fault=True, enable="1"),
    PgOptIn(pg_test=True, pg_fault=True, enable="1", fault_enable="true"),
])
def test_fault_gate_never_bypasses_base_or_independent_switch(settings, monkeypatch, gate):
    driver = Mock()
    monkeypatch.setattr(engines, "open_driver_connection", driver)
    with pytest.raises(PgSafetyError):
        engines.create_test_engine(settings, gate, fault=True)
    driver.assert_not_called()
