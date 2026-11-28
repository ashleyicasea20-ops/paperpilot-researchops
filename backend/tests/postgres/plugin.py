"""Actual pytest options/phases control live access; intent objects do not."""
import os
import pytest
from tests.postgres import runtime
from tests.postgres.guards import _deny,install_guards,reject_preexisting_engines
from tests.postgres.settings import PgOptIn,PgSafetyError,load_settings

install_guards()
PG_MARKERS=("integration","postgres_integration","postgres_concurrency","postgres_fault_injection")

def pytest_addoption(parser):
    group=parser.getgroup("paperpilot PostgreSQL")
    group.addoption("--pg-test",action="store_true",help="Use only via run_pg_tests.py")
    group.addoption("--pg-fault",action="store_true",help="Additional approved fault-test opt-in")

def get_opt_in(config):
    return PgOptIn(config.getoption("--pg-test"),config.getoption("--pg-fault"),
                   os.environ.get("PAPERPILOT_PG_TEST_ENABLE"),os.environ.get("PAPERPILOT_PG_TEST_FAULT_ENABLE"))

def pytest_configure(config):
    from app.db import session as db
    db.get_engine=_deny
    db.get_session_factory=_deny
    try:
        runtime.configure(config)
        reject_preexisting_engines()
        gate=get_opt_in(config)
        if gate.pg_test:gate.require(fault=gate.pg_fault)
        elif gate.pg_fault:gate.require(fault=True)
    except PgSafetyError as error:
        raise pytest.UsageError(str(error)) from None

def pytest_collection_modifyitems(config,items):
    gate=get_opt_in(config)
    for item in items:
        if any(item.get_closest_marker(m) for m in PG_MARKERS):
            try:gate.require(fault=item.get_closest_marker("postgres_fault_injection") is not None)
            except PgSafetyError:
                item.add_marker(pytest.mark.skip(reason="PostgreSQL tests require controlled-process opt-in"))

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item,nextitem):
    runtime._begin(item)
    try:yield
    finally:runtime._end(item)

def pytest_runtest_setup(item):
    runtime._set_phase("setup")

def pytest_runtest_call(item):
    runtime._set_phase("call")

def pytest_runtest_teardown(item,nextitem):
    runtime._set_phase("teardown")

@pytest.fixture
def pg_test_engine(request):
    engine=None
    failed=False
    try:
        gate=get_opt_in(request.config)
        runtime.permission(fault=request.node.get_closest_marker("postgres_fault_injection") is not None)
        settings=load_settings()
        from tests.postgres.engine import create_test_engine
        engine=create_test_engine(settings,gate,fault=gate.pg_fault)
    except PgSafetyError:
        failed=True
    if failed:pytest.fail("PostgreSQL runtime authorization refused; details suppressed",pytrace=False)
    try:yield engine
    finally:
        outstanding=engine.pool.checkedout()
        engine.dispose()
        if outstanding:
            request.session.shouldstop="PostgreSQL connections remain borrowed"
            pytest.fail("Borrowed connections require manual reconciliation",pytrace=False)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item,call):
    outcome=yield
    report=outcome.get_result()
    if report.failed and any(item.get_closest_marker(m) for m in PG_MARKERS):
        item.session.shouldstop="PostgreSQL test failed; stop further database operations"
