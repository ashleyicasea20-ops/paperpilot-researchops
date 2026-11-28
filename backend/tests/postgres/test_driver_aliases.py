"""Saved Python aliases use real driver chains; saved C references remain a boundary."""
import pytest
from tests.postgres.test_review_regressions import _child,BACKEND

@pytest.mark.parametrize("kind",["module","sync","async","binary_export"])
def test_supported_alias_paths_are_blocked_before_native_driver(kind):
    row=_child("""
import asyncio,json,pytest,psycopg
saved_module=psycopg.connect
saved_sync=psycopg.Connection.connect
saved_async=psycopg.AsyncConnection.connect
class Probe:
    def pytest_sessionstart(self,session):
        from tests.postgres.errors import PgSafetyError
        try:
            if KIND=="module":saved_module()
            elif KIND=="sync":saved_sync()
            elif KIND=="async":
                with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                    runner.run(saved_async())
            else:
                import psycopg_binary.pq
                psycopg_binary.pq.PGconn.connect_start(b"")
        except PgSafetyError:pass
        else:raise AssertionError("Supported path bypassed guards")
code=pytest.main(["--collect-only","-q","-p","no:cacheprovider",LEGACY],plugins=[Probe()])
print("PROBE="+json.dumps(dict(driver=counts["driver"],exit=code)))
""".replace("KIND",repr(kind)).replace("LEGACY",repr(str(BACKEND/"tests/test_database_integration.py"))))
    assert row==dict(driver=0,exit=0)

def test_saved_native_pointer_is_explicitly_not_a_monkeypatch_guarantee():
    row=_child("""
import json,pytest
from psycopg_binary.pq import PGconn
saved_native=PGconn.connect_start
class Probe:
    def pytest_sessionstart(self,session):
        from tests.postgres._physical_fence import PhysicalBlocked
        try:saved_native(b"")
        except PhysicalBlocked:pass
        else:raise AssertionError("Independent native substitute missing")
code=pytest.main(["--collect-only","-q","-p","no:cacheprovider",LEGACY],plugins=[Probe()])
print("PROBE="+json.dumps(dict(driver=counts["driver"],exit=code)))
""".replace("LEGACY",repr(str(BACKEND/"tests/test_database_integration.py"))))
    # A captured native pointer cannot be revoked by replacing module exports.
    # No real pointer is called here: the independently captured substitute fires.
    assert row==dict(driver=1,exit=0)
