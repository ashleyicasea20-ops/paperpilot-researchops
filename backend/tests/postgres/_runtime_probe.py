"""Explicitly selected, externally fenced runtime probe; not native DB evidence."""
import os
import pytest
from tests.postgres.engine import create_test_engine
from tests.postgres.settings import PgTestSettings,PgOptIn,PgSafetyError

def _runtime_check(caplog,*,fault=False):
    from tests.postgres import _physical_fence,guards
    assert _physical_fence.installed and guards._driver_generator.__module__=="tests.postgres._physical_fence"
    caplog.set_level("DEBUG",logger="psycopg")
    settings=PgTestSettings()
    gate=PgOptIn(pg_test=True,enable="1",pg_fault=fault,fault_enable="1" if fault else None)
    engine=create_test_engine(settings,gate,fault=fault)
    try:
        with pytest.raises(PgSafetyError) as caught:
            engine.connect()
        assert settings.password.get_secret_value() not in str(caught.value)+caplog.text
        before=_physical_fence.counts["driver"]
        with pytest.raises(PgSafetyError):engine.connect()
        assert _physical_fence.counts["driver"]==before
    finally:
        engine.dispose()
    print("RUNTIME_CHILD_PID="+str(os.getpid()))
    assert "DB_PASSWORD" not in os.environ and "JWT_SECRET_KEY" not in os.environ

@pytest.mark.postgres_integration
def test_actual_runtime_permission(caplog):
    _runtime_check(caplog)

@pytest.mark.postgres_fault_injection
def test_actual_fault_permission(caplog):
    _runtime_check(caplog,fault=True)
