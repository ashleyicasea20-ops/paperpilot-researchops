"""Reviewed probe plugin: observe guards before pytest's initial conftests."""
import pytest

_early=False

@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config,parser,args):
    global _early
    from tests.postgres import guards,runtime
    from tests.postgres.errors import PgSafetyError
    if not guards._installed:
        raise RuntimeError("Guards must precede initial conftest hooks")
    import psycopg
    from psycopg_binary import pq
    for connector in (psycopg.Connection.connect,lambda:pq.PGconn.connect_start(b"")):
        try:connector()
        except PgSafetyError:pass
        else:raise RuntimeError("Early hook unexpectedly authorized a driver")
    try:runtime.permission()
    except PgSafetyError:pass
    else:raise RuntimeError("Collection/startup must not issue permission")
    _early=True

def pytest_sessionfinish(session,exitstatus):
    from tests.postgres import runtime
    from tests.postgres.errors import PgSafetyError
    try:runtime.permission()
    except PgSafetyError:pass
    else:raise RuntimeError("Permission survived the test lifecycle")
    if not _early:
        raise RuntimeError("Early probe was not executed")
    reporter=session.config.pluginmanager.get_plugin("terminalreporter")
    reporter.write_line("STARTUP_GUARD_ACTIVE; SESSION_PERMISSION_ENDED")
