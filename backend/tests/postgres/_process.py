"""Independent subprocess observer. It never pre-installs project guards."""
import os
from pathlib import Path
import subprocess
import sys

BACKEND=Path(__file__).resolve().parents[2]

def run_pytest(arguments,*,extra_environment=None,forbid_settings=False,cwd=None,
               controlled=None,parent=False,prelude=""):
    args=list(arguments)
    if controlled is None:
        controlled="--pg-test" in args or "--pg-fault" in args
    if controlled:
        args=[arg for i,arg in enumerate(args) if not
              (arg=="-p" and i+1<len(args) and args[i+1]=="no:cacheprovider") and
              not (arg=="no:cacheprovider" and i and args[i-1]=="-p")]
    env={k:v for k,v in os.environ.items()
         if not k.startswith(("PG","DB_","PAPERPILOT_PG_TEST_","PYTEST_")) and k!="JWT_SECRET_KEY"}
    env.update(PYTHONDONTWRITEBYTECODE="1",PYTHONIOENCODING="utf-8",PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.update(extra_environment or {})
    prefix=("import sys,atexit,json,os\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(BACKEND))+")\n"
            "from tests.postgres._physical_fence import install,counts\ninstall("+repr(str(BACKEND))+")\n"
            "_loads=[]\n"
            "def _profile(frame,event,arg):\n"
            "    if event=='call' and frame.f_code.co_name=='__init__' and frame.f_code.co_filename.replace(chr(92),'/').endswith('/tests/postgres/settings.py'):_loads.append(1)\n"
            "sys.setprofile(_profile)\n"
            "atexit.register(lambda:print(('UNAUTHORIZED_DRIVER_CALLS=0' if counts['driver']==0 else 'DRIVER_SUBSTITUTES='+str(counts['driver']))))\n"
            "atexit.register(lambda:print('SETTINGS_LOADS='+str(len(_loads))))\n")
    if parent:
        # Exercise the public launcher, and externally fence its actual new child.
        child=prefix+"import runpy\nsys.argv="+repr([str(BACKEND/"tests/postgres/bootstrap.py"),*args])+"\nrunpy.run_path(sys.argv[0],run_name='__main__')\n"
        code=prefix+prelude+"""
import subprocess,runpy
_actual_run=subprocess.run
def _spawn(command,*a,**k):
    if isinstance(command,(list,tuple)) and any(str(value).endswith("bootstrap.py") for value in command):
        command=[command[0],"-I","-B","-c",CHILD]
    return _actual_run(command,*a,**k)
subprocess.run=_spawn
print("PARENT_PROCESS="+str(os.getpid()))
sys.argv=PARENT_ARGS
runpy.run_path(sys.argv[0],run_name="__main__")
""".replace("CHILD",repr(child)).replace("PARENT_ARGS",repr([str(BACKEND/"run_pg_tests.py"),*args]))
    elif controlled:
        code=prefix+prelude+"import runpy\nsys.argv="+repr([str(BACKEND/"tests/postgres/bootstrap.py"),*args])+"\nrunpy.run_path(sys.argv[0],run_name='__main__')\n"
    else:
        code=prefix+prelude+"import pytest\nraise SystemExit(pytest.main("+repr(["-p","no:cacheprovider",*args])+"))\n"
    command=[sys.executable,"-B","-c",code]
    if controlled or parent:
        command.insert(1,"-I")
    result=subprocess.run(command,cwd=cwd or BACKEND,env=env,capture_output=True,
                          text=True,encoding="utf-8",timeout=60)
    from tests.postgres._physical_fence import observe
    observe(result)
    return result
