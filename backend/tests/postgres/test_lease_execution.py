"""Real SQLAlchemy Connection/Session/Transaction against synthetic DBAPI."""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.postgres import policy,safety,runtime
from tests.postgres.engine import create_offline_test_engine
from tests.postgres.settings import PgSafetyError

@pytest.mark.parametrize("operation",["execute","driver_sql","connection_commit","transaction_commit","session_commit"])
@pytest.mark.parametrize("ending",["fuse","dispose"])
def test_all_execution_and_commit_paths_respect_engine_end(operation,ending,settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=create_offline_test_engine(settings,opt_in)
    session=None
    if operation=="session_commit":
        session=Session(engine)
        connection=session.connection()
    else:
        connection=engine.connect()
    transaction=connection.get_transaction() or connection.begin()
    lab=engine._review_lab
    if ending=="fuse":policy.trip(engine)
    else:engine.dispose()
    before=(len(lab.statements),lab.commits,lab.created)
    try:
        with pytest.raises(PgSafetyError):
            if operation=="execute":connection.execute(text("UPDATE workspaces SET name='synthetic'"))
            elif operation=="driver_sql":connection.exec_driver_sql("UPDATE workspaces SET name='synthetic'")
            elif operation=="connection_commit":connection.commit()
            elif operation=="transaction_commit":transaction.commit()
            else:session.commit()
        assert (len(lab.statements),lab.commits,lab.created)==before
    finally:
        if session:session.close()
        else:connection.close()
        engine.dispose()

def test_pool_reuse_requires_a_new_logical_lease(settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=create_offline_test_engine(settings,opt_in)
    a=engine.connect()
    original=a.connection.driver_connection
    safety.require_verified(a)
    a.close()
    with pytest.raises(PgSafetyError):safety.require_verified(a)
    b=engine.connect()
    try:
        safety.require_verified(b)
        # The physical connection is reused; its public checkout handle must
        # rotate so a retained handle cannot inherit the next caller's lease.
        current=b.connection.driver_connection
        assert current is not original
        assert current._native is original._native
        with pytest.raises(PgSafetyError):original.commit()
        assert engine._review_lab.created==1
        assert a is not b
    finally:b.close();engine.dispose()

def test_offline_factory_cannot_be_used_as_live_engine(settings,opt_in):
    from tests.postgres.engine import require_test_engine
    engine=create_offline_test_engine(settings,opt_in)
    try:
        with pytest.raises(PgSafetyError):require_test_engine(engine)
    finally:engine.dispose()

def test_controller_cannot_be_started_from_ordinary_test():
    with pytest.raises(PgSafetyError):runtime._start_controller()
