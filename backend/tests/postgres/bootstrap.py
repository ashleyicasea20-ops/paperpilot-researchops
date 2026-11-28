"""The only supported live-PG process entry. No plugin runs before guards."""
import os
from pathlib import Path
import sys

BACKEND=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(BACKEND))

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    from tests.postgres.errors import PgSafetyError
    try:
        from tests.postgres.guards import install_guards
        install_guards()
        from tests.postgres import runtime
        runtime._start_controller()
        from tests.postgres.launcher import arguments,environment
        args,plugins=arguments(sys.argv[1:])
        clean=environment("--pg-test" in args)
        os.environ.clear()
        os.environ.update(clean)
        if "--pg-test" in args and os.environ.get("PAPERPILOT_PG_TEST_ENABLE")!="1":
            raise PgSafetyError("Actual environment opt-in is required")
        if "--pg-fault" in args and ("--pg-test" not in args or os.environ.get("PAPERPILOT_PG_TEST_FAULT_ENABLE")!="1"):
            raise PgSafetyError("Fault opt-in is incomplete")
        from tests.postgres.guards import reject_preexisting_engines
        reject_preexisting_engines()
        import importlib
        import pytest
        from tests.postgres import plugin
        approved=[plugin]+[importlib.import_module(name) for name in plugins]
        os.chdir(BACKEND)
        return pytest.main(["-c",str(BACKEND/"pytest.ini"),"-p","no:cacheprovider",*args],plugins=approved)
    except PgSafetyError:
        print("Controlled test startup refused; no database operation was authorized",file=sys.stderr)
        return 4

if __name__=="__main__":
    raise SystemExit(main())
