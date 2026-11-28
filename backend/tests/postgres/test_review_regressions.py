"""F1-F8 regressions, independent physical fences and actual SQLAlchemy leases."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
from sqlalchemy import text

from tests.postgres import engine as engines, safety
from tests.postgres.settings import PgOptIn, PgSafetyError
from tests.postgres._synthetic import policy_engine

BACKEND = Path(__file__).resolve().parents[2]


def test_f1_forged_intent_cannot_open_driver(settings, monkeypatch):
    driver = Mock(side_effect=AssertionError("Physical driver reached"))
    monkeypatch.setattr("tests.postgres.guards._driver_connect",driver)
    engine = None
    try:
        try:
            engine = engines.create_test_engine(settings,PgOptIn(pg_test=True,enable="1"))
            engine.connect()
        except PgSafetyError:
            pass
    finally:
        if engine is not None:
            engine.dispose()
    driver.assert_not_called()


def test_f5_invalidated_lease_cannot_reconnect(settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine = policy_engine(settings,opt_in)
    connection = engine.connect()
    lab = engine._review_lab
    connection.invalidate()
    before = lab.created
    statements = len(lab.statements)
    try:
        with pytest.raises(PgSafetyError):
            safety.verify_connection(connection,settings,opt_in)
        assert lab.created == before
        assert len(lab.statements) == statements
        with pytest.raises(PgSafetyError):
            safety.require_verified(connection)
    finally:
        connection.close()
        engine.dispose()


def test_f6_revocation_survives_all_cleanup_faults(settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine = policy_engine(settings,opt_in)
    connection = engine.connect()
    lab = engine._review_lab
    before = len(lab.statements)
    try:
        with monkeypatch.context() as patch:
            for name in ("rollback","invalidate","close"):
                patch.setattr(connection,name,Mock(side_effect=RuntimeError("synthetic cleanup")))
            with pytest.raises(PgSafetyError):
                safety.verify_connection(connection,settings,PgOptIn())
            with pytest.raises(PgSafetyError):
                safety.require_verified(connection)
            with pytest.raises(PgSafetyError):
                connection.exec_driver_sql("UPDATE public.workspaces SET name='synthetic'")
        assert len(lab.statements) == before
    finally:
        connection.close()
        engine.dispose()


def test_f7_fuse_blocks_already_borrowed_lease(settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine = policy_engine(settings,opt_in)
    a = engine.connect()
    lab = engine._review_lab
    bad = list(snapshot)
    bad[0] = dict(snapshot[0],database_name="unapproved")
    monkeypatch.setattr(safety,"read_snapshot",lambda c:tuple(bad))
    try:
        with pytest.raises(PgSafetyError):
            engine.connect()
        before = len(lab.statements)
        with pytest.raises(PgSafetyError):
            a.exec_driver_sql("UPDATE public.workspaces SET name='synthetic'")
        assert len(lab.statements) == before
        created = lab.created
        with pytest.raises(PgSafetyError):
            engine.connect()
        assert lab.created == created
    finally:
        a.close()
        engine.dispose()


def _child(code):
    import os
    env={k:v for k,v in os.environ.items() if not k.startswith(("PG","DB_","PAPERPILOT_PG_TEST_")) and k!="JWT_SECRET_KEY"}
    env.update(PYTHONDONTWRITEBYTECODE="1",PYTHONIOENCODING="utf-8",PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    prefix="from tests.postgres._physical_fence import install,counts\ninstall("+repr(str(BACKEND))+")\n"
    result=subprocess.run([sys.executable,"-B","-c",prefix+code],cwd=BACKEND,env=env,
                          capture_output=True,text=True,encoding="utf-8",timeout=40)
    from tests.postgres._physical_fence import observe
    observe(result)
    assert result.returncode == 0,result.stdout+result.stderr
    rows=[json.loads(line.split("=",1)[1]) for line in result.stdout.splitlines() if line.startswith("PROBE=")]
    assert rows,result.stdout+result.stderr
    return rows[-1]


@pytest.mark.parametrize("kind",["engine","session","connection","legacy"])
def test_f2_cached_objects_are_rejected(kind):
    row=_child("""
import json,pytest
from sqlalchemy.orm import Session
from sqlalchemy import text
from tests.postgres._synthetic import Lab,bare_engine
lab=Lab();engine=bare_engine(lab)
borrowed=engine.connect()
borrowed.exec_driver_sql("SELECT 1")
session=Session(engine)
baseline=len(lab.statements)
direct_legacy_rejected=True
if KIND=="legacy":
    from tests.test_database_integration import test_real_postgresql_select_one as legacy_probe
    from tests.postgres.errors import PgSafetyError
    try:legacy_probe(engine)
    except PgSafetyError:pass
    else:direct_legacy_rejected=False
class Probe:
    def pytest_sessionstart(self,session):
        try:
            if KIND=="engine":
                with engine.connect() as c:c.exec_driver_sql("UPDATE public.workspaces SET name='synthetic'")
            elif KIND=="session":
                globals()["session"].execute(text("UPDATE public.workspaces SET name='synthetic'"))
            elif KIND=="connection":
                borrowed.exec_driver_sql("UPDATE public.workspaces SET name='synthetic'")
            else:
                from tests.test_database_integration import test_real_postgresql_select_one
                test_real_postgresql_select_one(engine)
        except Exception:pass
code=pytest.main(["--collect-only","-q","-p","no:cacheprovider",LEGACY],plugins=[Probe()])
from tests.postgres import guards
print("PROBE="+json.dumps(dict(sql=len(lab.statements)-baseline,driver=counts["driver"],exit=code,fresh_refusal=(code==4),guards_installed=guards._installed,direct_legacy_rejected=direct_legacy_rejected)))
""".replace("KIND",repr(kind)).replace("LEGACY",repr(str(BACKEND/"tests/test_database_integration.py"))))
    assert row["sql"]==0
    assert row["driver"]==0
    assert row["exit"] == 4
    assert row["fresh_refusal"] is True
    assert row["guards_installed"] is True
    assert row["direct_legacy_rejected"] is True


def test_f3_native_export_is_guarded():
    row=_child("""
import importlib,json,pytest,psycopg
origin="psycopg_binary.pq"
class Probe:
    def pytest_sessionstart(self,session):
        try:importlib.import_module(origin).PGconn.connect_start(b"")
        except Exception:pass
code=pytest.main(["--collect-only","-q","-p","no:cacheprovider",LEGACY],plugins=[Probe()])
print("PROBE="+json.dumps(dict(driver=counts["driver"],exit=code)))
""".replace("LEGACY",repr(str(BACKEND/"tests/test_database_integration.py"))))
    assert row["driver"]==0


def test_f1_collection_cannot_grant_itself_permission():
    row=_child("""
import json,pytest
class Probe:
    def pytest_collection(self,session):
        from tests.postgres.settings import PgTestSettings,PgOptIn
        from tests.postgres.engine import create_test_engine
        e=None
        try:
            s=PgTestSettings(enable="1",host="127.0.0.1",port=55432,db_name="paperpilot_concurrency_test",user="paperpilot_test_app",password="synthetic",environment_id="ab756a0b-8c2d-4b21-b34f-df010f111291")
            e=create_test_engine(s,PgOptIn(pg_test=True,enable="1"));e.connect()
        except Exception:pass
        finally:
            if e is not None:e.dispose()
code=pytest.main(["--collect-only","-q","-p","no:cacheprovider",LEGACY],plugins=[Probe()])
print("PROBE="+json.dumps(dict(driver=counts["driver"],exit=code)))
""".replace("LEGACY",repr(str(BACKEND/"tests/test_database_integration.py"))))
    assert row["driver"]==0


def test_f4_f8_controlled_entry_exists_before_plugin_loading():
    launcher=BACKEND/"run_pg_tests.py"
    assert launcher.is_file(),"F4/F8 require a fresh-process controlled entry, not conftest ordering"


def test_closed_engine_cannot_keep_borrowed_connection(settings,opt_in,snapshot,monkeypatch):
    monkeypatch.setattr(safety,"read_snapshot",lambda c:snapshot)
    engine=policy_engine(settings,opt_in)
    connection=engine.connect()
    lab=engine._review_lab
    engine.dispose()
    before=len(lab.statements)
    try:
        with pytest.raises(PgSafetyError):
            connection.exec_driver_sql("UPDATE public.workspaces SET name='synthetic'")
        assert len(lab.statements)==before
    finally:
        connection.close()
