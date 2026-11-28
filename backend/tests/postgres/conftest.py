"""Offline-only fixtures. All server responses below are synthetic mocks."""

from copy import deepcopy
import os
from unittest.mock import Mock

import pytest

from tests.postgres.settings import PgOptIn, PgTestSettings
from tests.postgres.safety import OWNER, PURPOSE, REVISION, expected_schema


@pytest.fixture(autouse=True)
def isolated_test_environment(monkeypatch, request):
    if any(request.node.get_closest_marker(name) for name in ('integration','postgres_integration','postgres_concurrency','postgres_fault_injection')):
        return
    for key in tuple(os.environ):
        if key.startswith(("PG", "PAPERPILOT_PG_TEST_")):
            monkeypatch.delenv(key)


@pytest.fixture
def values():
    return dict(enable="1", host="127.0.0.1", port=55432,
                db_name="paperpilot_concurrency_test", user="paperpilot_test_app",
                password="synthetic-pg-private-sentinel@:/?#",
                environment_id="54c232b5-33af-4dbc-85c7-72a0b7892074")


@pytest.fixture
def settings(values):
    return PgTestSettings(**values)


@pytest.fixture
def opt_in():
    return PgOptIn(pg_test=True, enable="1")


@pytest.fixture
def snapshot(settings):
    identity = dict(
        database_name=settings.db_name, session_user_name=settings.user,
        current_user_name=settings.user, address=settings.host, port=settings.port,
        version=170012, search_path="public", database_owner=OWNER, memberships=0,
        rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolreplication=False,
        rolbypassrls=False, rolcanlogin=True, schema_create=False,
        database_create=False, marker_write=False, database_temp=False, marker_schema_create=False,
        marker_owner=OWNER, version_owner=OWNER, version_write=False, effective_schemas=['pg_catalog', 'public'],
    )
    return (identity, {"environment_id": settings.environment_id, "purpose": PURPOSE},
            (REVISION,), dict(catalog_valid=True, tables_owned=True, dml_only=True),
            deepcopy(expected_schema()))


@pytest.fixture
def mock_connection():
    connection = Mock()
    connection.info = {}
    connection.closed = False
    connection.invalidated = False
    connection.in_transaction.return_value = False
    connection.dialect.name = "postgresql"
    connection.connection.driver_connection = object()
    return connection


@pytest.fixture
def guard_lab(settings, opt_in, snapshot, monkeypatch):
    """Real SQLAlchemy objects; only the built-in non-native DBAPI is simulated."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    import psycopg
    from sqlalchemy.engine import Engine
    from sqlalchemy.engine.default import DefaultDialect
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
    from sqlalchemy.pool import QueuePool
    from tests.postgres import engine as engines, safety
    from tests.postgres._synthetic import DBAPI
    from tests.postgres._guard_cases import Cursor, Lab

    monkeypatch.setattr(DBAPI, "cursor", lambda db, *a, **kw: Cursor(db.lab))
    for method, counter in (("commit", "commits"), ("rollback", "rollbacks"), ("close", "closes"),
                            ("tpc_begin", "tpc_begins"), ("tpc_prepare", "tpc_prepares"),
                            ("tpc_commit", "tpc_commits"), ("tpc_rollback", "tpc_rollbacks")):
        def count(db, *a, _counter=counter, **kw):
            setattr(db.lab, _counter, getattr(db.lab, _counter) + 1)
        monkeypatch.setattr(DBAPI, method, count, raising=False)
    monkeypatch.setattr(safety, "read_snapshot", lambda _: snapshot)

    @contextmanager
    def build(*, psycopg_dialect=False):
        lab = Lab()
        if psycopg_dialect:
            dialect = PGDialect_psycopg(dbapi=psycopg, use_native_hstore=False)
        else:
            dialect = DefaultDialect(dbapi=psycopg, paramstyle="named")
            dialect.name, dialect.driver = "postgresql", "guard-synthetic"
        engine = Engine(QueuePool(lab.connect, pool_size=4, max_overflow=0), dialect,
                        settings.database_url, hide_parameters=True)
        engines._attach(engine, settings, opt_in, mode="synthetic")
        try:
            yield SimpleNamespace(engine=engine, lab=lab)
            assert engine.pool.checkedout() == 0
        finally:
            engine.dispose()

    return build
