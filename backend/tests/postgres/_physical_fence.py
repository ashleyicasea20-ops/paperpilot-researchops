"""Independent test-only libpq substitutes. Never installs project guards."""
import atexit
from collections import Counter
import json
import os
from pathlib import Path
import sys

counts = Counter()
installed = False
failure = None

def observe(result):
    """Aggregate independently fenced child counts, including public-launcher children."""
    for line in result.stdout.splitlines():
        if line.startswith("PHYSICAL_FENCE="):
            row=json.loads(line.split("=",1)[1])
            counts["subprocess_driver_substitutes"] += row.get("driver",0)
            counts["subprocess_environment_audits"] += row.get("environment_audit",0)

class PhysicalBlocked(RuntimeError):
    pass

def install(backend):
    global installed
    if installed:
        return
    installed=True
    target = os.path.normcase(os.path.realpath(Path(backend) / ".env"))
    def audit(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            if os.path.normcase(os.path.realpath(os.fsdecode(args[0]))) == target:
                counts["environment_audit"] += 1
                raise PermissionError("Independent fence blocked application environment")
    sys.addaudithook(audit)
    import psycopg
    from psycopg import generators, pq
    native = pq.PGconn
    generator = generators.connect
    def stopped(*a, **k):
        counts["driver"] += 1
        raise failure if failure is not None else PhysicalBlocked("Independent physical driver substitute")
    class FencedPG:
        connect = staticmethod(stopped)
        connect_start = staticmethod(stopped)
    # Leave Python Connection.connect intact so saved Python aliases still
    # exercise their real generator chain. Replace native/C entry points only.
    for name, module in tuple(sys.modules.items()):
        if module is not None and name.startswith("psycopg"):
            if getattr(module, "PGconn", None) is native:
                module.PGconn = FencedPG
            if getattr(module, "connect", None) is generator:
                module.connect = stopped
    generators.connect = stopped
    atexit.register(lambda: print("PHYSICAL_FENCE=" + json.dumps(dict(counts,pid=os.getpid()))))
    return FencedPG
