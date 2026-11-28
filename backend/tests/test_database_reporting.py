"""Database failure reporting must not expose driver/configuration secrets."""

from types import SimpleNamespace

from pydantic import ValidationError
import pytest
from sqlalchemy.exc import OperationalError

from tests import test_database_integration as integration


@pytest.mark.parametrize("source", ["driver", "configuration"])
def test_integration_failure_suppresses_sensitive_exception_context(source):
    marker = "private-test-sentinel"
    if source == "driver":
        failure = OperationalError(None, {"password": marker}, RuntimeError(marker))
    else:
        failure = ValidationError.from_exception_data("Settings", [{
            "type": "int_parsing", "loc": ("db_port",), "input": marker,
        }])

    def unavailable():
        raise failure

    engine = SimpleNamespace(connect=unavailable)
    with pytest.raises(pytest.fail.Exception) as caught:
        integration._check_select_one(engine)

    assert marker not in str(caught.value)
    assert type(failure).__name__ in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.pytrace is False
