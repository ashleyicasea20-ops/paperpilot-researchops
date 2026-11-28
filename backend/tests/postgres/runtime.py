"""Runtime provenance, not a sandbox against hostile code in this interpreter."""
import os
from pathlib import Path
import sys
from threading import RLock
from tests.postgres.errors import PgSafetyError

_lock = RLock()
_controller = None
_config = None
_current = None
_phase = None
_MARKERS = ("integration","postgres_integration","postgres_concurrency","postgres_fault_injection")

def _start_controller():
    global _controller
    expected = Path(__file__).with_name("bootstrap.py").resolve()
    main = getattr(sys.modules.get("__main__"),"__file__",None)
    if not sys.flags.isolated or main is None or Path(main).resolve()!=expected or Path(sys.argv[0]).resolve()!=expected:
        raise PgSafetyError("A fresh isolated controlled test process is required")
    if _controller is not None:
        raise PgSafetyError("The test controller is already initialized")
    _controller = (os.getpid(),object())

def configure(config):
    global _config
    if config.getoption("--pg-test") or config.getoption("--pg-fault"):
        if _controller is None or _controller[0]!=os.getpid():
            raise PgSafetyError("PostgreSQL options require run_pg_tests.py")
    if _controller is not None:
        import pytest
        if not isinstance(config,pytest.Config):
            raise PgSafetyError("An actual pytest Config is required")
        _config=config

def _begin(item):
    global _current,_phase
    with _lock:
        _current=(item,object())
        _phase="setup"

def _set_phase(phase):
    global _phase
    with _lock:_phase=phase

def _end(item):
    global _current,_phase
    with _lock:
        if _current is not None and _current[0] is item:
            _current=None
            _phase=None

def permission(*,fault=False):
    with _lock:
        if (_controller is None or _controller[0]!=os.getpid() or _config is None
                or _current is None or _phase not in ("setup","call","teardown")):
            raise PgSafetyError("No runtime PostgreSQL permission")
        item,nonce=_current
        if item.config is not _config or not any(item.get_closest_marker(m) for m in _MARKERS):
            raise PgSafetyError("Only the current marked PostgreSQL test may connect")
        if not _config.getoption("--pg-test") or os.environ.get("PAPERPILOT_PG_TEST_ENABLE")!="1":
            raise PgSafetyError("Actual CLI and environment opt-in are required")
        is_fault=fault or item.get_closest_marker("postgres_fault_injection") is not None
        if is_fault and (not _config.getoption("--pg-fault") or os.environ.get("PAPERPILOT_PG_TEST_FAULT_ENABLE")!="1"):
            raise PgSafetyError("Actual fault opt-in is required")
        return (_controller,nonce)

def check(owner,*,fault=False):
    if permission(fault=fault)!=owner:
        raise PgSafetyError("The PostgreSQL test permission has expired")
