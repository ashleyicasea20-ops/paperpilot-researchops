"""Real startup-chain tests: only an independent physical fence precedes pytest."""
import pytest
from tests.postgres._process import run_pytest

@pytest.mark.parametrize("environment", [{}, {"PAPERPILOT_PG_TEST_ENABLE": "1"}])
def test_old_integration_is_safe_by_default(environment):
    result = run_pytest(["-q", "tests/test_database_integration.py"], extra_environment=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert "UNAUTHORIZED_DRIVER_CALLS=0" in result.stdout


@pytest.mark.parametrize("arguments,environment", [
    (["--pg-test"], {}),
    (["--pg-fault"], {"PAPERPILOT_PG_TEST_FAULT_ENABLE": "1"}),
    (["--pg-test", "--pg-fault"], {"PAPERPILOT_PG_TEST_ENABLE": "1"}),
])
def test_incomplete_cli_authorization_is_rejected(arguments, environment):
    result = run_pytest([*arguments, "-q", "tests/test_database_integration.py"],
                        extra_environment=environment)
    assert result.returncode == 4
    assert "UNAUTHORIZED_DRIVER_CALLS=0" in result.stdout


@pytest.mark.parametrize("opt_in", [False, True])
def test_collection_loads_no_database_credentials_or_connections(opt_in):
    args = ["--collect-only", "-q", "tests/test_database_integration.py"]
    environment = {}
    if opt_in:
        args.insert(0, "--pg-test")
        environment["PAPERPILOT_PG_TEST_ENABLE"] = "1"
    result = run_pytest(args, extra_environment=environment, forbid_settings=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout
    assert "UNAUTHORIZED_DRIVER_CALLS=0" in result.stdout
    assert "SETTINGS_LOADS=0" in result.stdout
