"""Real PostgreSQL dialect initialization and library logging, entirely offline."""
import logging
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.base import PGDialect
from tests.postgres import engine as engines,safety
from tests.postgres._synthetic import Lab,DBAPI
from tests.postgres.settings import PgOptIn,PgSafetyError
from tests.postgres._process import BACKEND,run_pytest

class ReviewDialect(PGDialect):
    driver="synthetic"
    supports_statement_cache=True

def test_real_postgresql_dialect_initializes_before_business_lease(settings,opt_in,snapshot,monkeypatch):
    registry.register("postgresql.reviewsynthetic",__name__,"ReviewDialect")
    lab=Lab()
    values={"select pg_catalog.version()":"PostgreSQL 17.12 on synthetic",
            "select current_schema()":"public",
            "show transaction isolation level":"read committed",
            "show standard_conforming_strings":"on"}
    from tests.postgres._synthetic import Cursor
    original=Cursor.execute
    def execute(cursor,statement,parameters=None):
        original(cursor,statement,parameters)
        cursor.value=values.get(statement.strip().lower(),1)
        cursor.description=[("value",None,None,None,None,None,None)]
    monkeypatch.setattr(Cursor,"execute",execute)
    monkeypatch.setattr(Cursor,"fetchone",lambda c:(c.value,))
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=create_engine("postgresql+reviewsynthetic://",creator=lab.connect,
                         module=SimpleNamespace(paramstyle="named",Error=RuntimeError))
    engines._attach(engine,settings,opt_in,mode="synthetic")
    try:
        with engine.connect() as connection:
            safety.require_verified(connection)
            assert engine.dialect.server_version_info==(17,12)
            assert engine.dialect.default_schema_name=="public"
            assert engine.dialect.default_isolation_level=="READ COMMITTED"
    finally:engine.dispose()
    assert lab.created==1

def test_pool_cleanup_logs_do_not_expose_diagnostics(settings,opt_in,snapshot,monkeypatch,caplog):
    marker="synthetic-pool-cleanup-private-marker"
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=engines.create_offline_test_engine(settings,opt_in)
    connection=engine.connect()
    def fail(*a,**k):raise RuntimeError(marker)
    with monkeypatch.context() as patch:
        patch.setattr(DBAPI,"rollback",fail)
        patch.setattr(DBAPI,"close",fail)
        with pytest.raises(PgSafetyError):
            safety.verify_connection(connection,settings,PgOptIn())
    try:
        assert marker not in caplog.text
        assert caplog.records
    finally:
        connection.close()
        engine.dispose()

def test_psycopg_debug_logs_do_not_expose_native_errors():
    marker="synthetic-native-private-marker"
    env=dict(PAPERPILOT_PG_TEST_ENABLE="1",PAPERPILOT_PG_TEST_HOST="127.0.0.1",
             PAPERPILOT_PG_TEST_PORT="55432",PAPERPILOT_PG_TEST_DB_NAME="paperpilot_concurrency_test",
             PAPERPILOT_PG_TEST_USER="paperpilot_test_app",PAPERPILOT_PG_TEST_PASSWORD=marker,
             PAPERPILOT_PG_TEST_ENVIRONMENT_ID="d36d4edc-fbb5-4c6c-880b-a9bbb433c617")
    prelude=("import logging,psycopg\nlogging.basicConfig(level=logging.DEBUG)\n"
             "from tests.postgres import _physical_fence\n"
             "_physical_fence.failure=psycopg.OperationalError("+repr(marker)+")\n")
    result=run_pytest(["--pg-test","-q",str(BACKEND/"tests/postgres/_runtime_probe.py")+"::test_actual_runtime_permission"],
                      extra_environment=env,controlled=True,prelude=prelude)
    output=result.stdout+result.stderr
    assert result.returncode==0,output
    assert "DRIVER_SUBSTITUTES=1" in output
    assert marker not in output

@pytest.mark.parametrize("statement",[
    "UPDATE workspaces SET name='synthetic'",
    "SELECT 1",
    "select pg_catalog.version(); UPDATE workspaces SET name='synthetic'",
    "select pg_catalog.version() -- unexpected",
])
def test_dialect_initialization_cannot_issue_business_sql(statement,settings,opt_in):
    registry.register("postgresql.reviewreject",__name__,"RejectingDialect")
    lab=Lab()
    engine=create_engine("postgresql+reviewreject://",creator=lab.connect,
                         module=SimpleNamespace(paramstyle="named",Error=RuntimeError))
    engine.dialect.review_statement=statement
    engines._attach(engine,settings,opt_in,mode="synthetic")
    try:
        with pytest.raises(Exception):
            engine.connect()
        from tests.postgres import policy
        assert policy.state(engine).failed
        assert lab.statements==[]
        created=lab.created
        with pytest.raises(PgSafetyError):engine.connect()
        assert lab.created==created
    finally:engine.dispose()

class RejectingDialect(ReviewDialect):
    def initialize(self,connection):
        connection.exec_driver_sql(self.review_statement)

def test_initialization_permission_expires_before_business_use(settings,opt_in,snapshot,monkeypatch):
    registry.register("postgresql.reviewexpired",__name__,"RetainingDialect")
    lab=Lab()
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=create_engine("postgresql+reviewexpired://",creator=lab.connect,
                         module=SimpleNamespace(paramstyle="named",Error=RuntimeError))
    engines._attach(engine,settings,opt_in,mode="synthetic")
    try:
        with engine.connect() as business:
            safety.require_verified(business)
            before=len(lab.statements)
            with pytest.raises(PgSafetyError):
                engine.dialect.retained.exec_driver_sql("select pg_catalog.version()")
            assert len(lab.statements)==before
    finally:engine.dispose()

class RetainingDialect(ReviewDialect):
    def initialize(self,connection):
        self.retained=connection
        # No dialect query is needed for this lifecycle-only negative test.

def test_initialization_callback_rejects_invalidated_connection_without_reconnect(settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=engines.create_offline_test_engine(settings,opt_in)
    connection=engine.connect()
    connection.invalidate()
    before=engine._review_lab.created
    try:
        with pytest.raises(PgSafetyError):engine.dialect.initialize(connection)
        assert engine._review_lab.created==before
    finally:connection.close();engine.dispose()
