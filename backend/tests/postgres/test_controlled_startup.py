"""Actual public/bootstrap startup; external native substitutes only."""
import json
import re
from pathlib import Path
import pytest
from tests.postgres._process import BACKEND,run_pytest

LEGACY=str(BACKEND/"tests/test_database_integration.py")
PROBE=str(BACKEND/"tests/postgres/_runtime_probe.py")
ENABLE={"PAPERPILOT_PG_TEST_ENABLE":"1"}

def fenced(result,*,exit=0,calls=0):
    output=result.stdout+result.stderr
    assert result.returncode==exit,output
    rows=[json.loads(s.split("=",1)[1]) for s in result.stdout.splitlines() if s.startswith("PHYSICAL_FENCE=")]
    assert rows,output
    assert sum(r.get("driver",0) for r in rows)==calls,output
    assert all(r.get("environment_audit",0)==0 for r in rows),output
    return output

@pytest.mark.parametrize("directory",["backend","repository","external"])
@pytest.mark.parametrize("controlled",[False,True])
def test_real_startup_from_different_working_directories(directory,controlled,tmp_path):
    cwd={"backend":BACKEND,"repository":BACKEND.parent,"external":tmp_path}[directory]
    args=["-q",LEGACY]
    if controlled:
        args=["--pg-test","--collect-only","-p","tests.postgres._startup_probe",*args]
    result=run_pytest(args,cwd=cwd,extra_environment=ENABLE,controlled=controlled)
    output=fenced(result)
    assert "SETTINGS_LOADS=0" in output
    assert ("STARTUP_GUARD_ACTIVE; SESSION_PERMISSION_ENDED" if controlled else "1 skipped") in output

def test_live_options_on_ordinary_pytest_are_refused():
    result=run_pytest(["--pg-test","--collect-only","-q",LEGACY],
                      extra_environment=ENABLE,controlled=False)
    assert "require run_pg_tests.py" in fenced(result,exit=4)

@pytest.mark.parametrize("args,env",[
    (["--pg-test"],{}),
    (["--pg-fault"],{"PAPERPILOT_PG_TEST_FAULT_ENABLE":"1"}),
    (["--pg-test","--pg-fault"],ENABLE),
    (["--pg-test","--pg-fault"],{"PAPERPILOT_PG_TEST_FAULT_ENABLE":"1"}),
])
def test_public_launcher_refuses_incomplete_actual_opt_in(args,env):
    result=run_pytest([*args,"--collect-only","-q",LEGACY],extra_environment=env,parent=True)
    fenced(result,exit=4)

@pytest.mark.parametrize("extra",[
    ["-p","unreviewed_plugin"],
    ["-o","addopts=--pg-test"],
    ["-c","unreviewed.ini"],
    [str(BACKEND.parent/"frontend")],
])
def test_public_launcher_rejects_unreviewed_options(extra):
    result=run_pytest(extra,parent=True)
    output=fenced(result,exit=4)
    assert "Controlled test startup refused" in output
    assert "SETTINGS_LOADS=0" in output

def test_plugin_environment_is_removed_before_pytest_loading():
    env=dict(ENABLE,PYTEST_PLUGINS="unreviewed_plugin",PYTEST_ADDOPTS="-p unreviewed_plugin")
    result=run_pytest(["--pg-test","--collect-only","-q","-p","tests.postgres._startup_probe",LEGACY],
                      extra_environment=env,parent=True)
    assert "STARTUP_GUARD_ACTIVE" in fenced(result)

def test_implicit_libpq_is_refused_not_silently_retargeted():
    result=run_pytest(["--pg-test","--collect-only","-q",LEGACY],
                      extra_environment=dict(ENABLE,PGSERVICE="unapproved"),parent=True)
    fenced(result,exit=4)

def test_environment_only_still_skips_native_test_via_public_launcher():
    result=run_pytest(["-q",LEGACY],extra_environment=ENABLE,parent=True)
    assert "1 skipped" in fenced(result)

def test_public_launcher_creates_new_pid_and_runtime_can_reach_only_fence():
    env=dict(ENABLE,PAPERPILOT_PG_TEST_HOST="127.0.0.1",PAPERPILOT_PG_TEST_PORT="55432",
             PAPERPILOT_PG_TEST_DB_NAME="paperpilot_concurrency_test",
             PAPERPILOT_PG_TEST_USER="paperpilot_test_app",
             PAPERPILOT_PG_TEST_PASSWORD="synthetic-runtime-private-marker",
             PAPERPILOT_PG_TEST_ENVIRONMENT_ID="d36d4edc-fbb5-4c6c-880b-a9bbb433c617",
             DB_PASSWORD="synthetic-development",JWT_SECRET_KEY="synthetic-development-jwt")
    result=run_pytest(["--pg-test","-q","-p","tests.postgres._startup_probe",PROBE+"::test_actual_runtime_permission"],
                      extra_environment=env,parent=True)
    output=fenced(result,calls=1)
    assert "1 passed" in output and "SESSION_PERMISSION_ENDED" in output
    assert env["PAPERPILOT_PG_TEST_PASSWORD"] not in output
    parent=int(re.search(r"PARENT_PROCESS=(\d+)",output).group(1))
    rows=[json.loads(s.split("=",1)[1]) for s in output.splitlines() if s.startswith("PHYSICAL_FENCE=")]
    assert len({r["pid"] for r in rows})==2
    assert any(r["pid"]!=parent and r.get("driver")==1 for r in rows)

@pytest.mark.parametrize("fault",[False,True])
def test_fault_marker_requires_additional_actual_opt_in(fault):
    env=dict(ENABLE,PAPERPILOT_PG_TEST_HOST="127.0.0.1",PAPERPILOT_PG_TEST_PORT="55432",
             PAPERPILOT_PG_TEST_DB_NAME="paperpilot_concurrency_test",
             PAPERPILOT_PG_TEST_USER="paperpilot_test_app",
             PAPERPILOT_PG_TEST_PASSWORD="synthetic-fault-only",
             PAPERPILOT_PG_TEST_ENVIRONMENT_ID="d36d4edc-fbb5-4c6c-880b-a9bbb433c617")
    args=["--pg-test","-q",PROBE+"::test_actual_fault_permission"]
    if fault:
        args.insert(0,"--pg-fault")
        env["PAPERPILOT_PG_TEST_FAULT_ENABLE"]="1"
    output=fenced(run_pytest(args,extra_environment=env,parent=True),calls=int(fault))
    assert ("1 passed" if fault else "1 skipped") in output
