"""Validate CLI and spawn an isolated interpreter; imports no pytest or settings."""
import configparser
import os
from pathlib import Path
import subprocess
import sys
from tests.postgres.errors import PgSafetyError

BACKEND=Path(__file__).resolve().parents[2]
PLUGINS=frozenset(("tests.postgres._startup_probe",))
FLAGS=frozenset(("-q","-v","-vv","-x","--collect-only","--pg-test","--pg-fault","--disable-warnings","--strict-markers"))

def arguments(argv):
    result=[];plugins=[];i=0
    while i<len(argv):
        arg=argv[i]
        if arg in FLAGS:
            result.append(arg);i+=1
        elif arg in ("-m","-k"):
            if i+1>=len(argv):raise PgSafetyError("Missing test selector")
            result.extend((arg,argv[i+1]));i+=2
        elif arg=="-p":
            if i+1>=len(argv) or argv[i+1] not in PLUGINS:
                raise PgSafetyError("Unapproved test plugin")
            plugins.append(argv[i+1]);i+=2
        elif arg.startswith("-"):
            raise PgSafetyError("Unsupported controlled test option")
        else:
            path,separator,node=arg.partition("::")
            resolved=Path(path).resolve()
            if not resolved.is_relative_to((BACKEND/"tests").resolve()):
                raise PgSafetyError("Only repository test paths are approved")
            result.append(str(resolved)+(separator+node if separator else ""));i+=1
    ini=configparser.ConfigParser()
    ini.read(BACKEND/"pytest.ini",encoding="utf-8")
    if set(ini.sections())!={"pytest"} or set(ini["pytest"])-{"markers"}:
        raise PgSafetyError("Unreviewed pytest configuration")
    return result,plugins

def environment(pg):
    if pg and any(key.startswith("PG") for key in os.environ):
        raise PgSafetyError("Implicit libpq environment settings are forbidden")
    result={key:value for key,value in os.environ.items()
            if not key.startswith(("DB_","PG","PYTEST_")) and key not in
            ("JWT_SECRET_KEY","PYTHONPATH","PYTHONSTARTUP","PYTHONINSPECT")}
    if not pg:
        result={key:value for key,value in result.items() if not key.startswith("PAPERPILOT_PG_TEST_")}
    result["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"
    result["PYTHONDONTWRITEBYTECODE"]="1"
    return result

def main(argv=None):
    try:
        argv=list(sys.argv[1:] if argv is None else argv)
        arguments(argv)
        env=environment("--pg-test" in argv)
    except PgSafetyError:
        print("Controlled test startup refused; check approved options and environment",file=sys.stderr)
        return 4
    return subprocess.run([sys.executable,"-I","-B",str(Path(__file__).with_name("bootstrap.py")),*argv],
                          env=env).returncode
