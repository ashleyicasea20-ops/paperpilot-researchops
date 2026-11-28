"""Opt-in SELECT 1 against the verified isolated PostgreSQL test instance."""

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from tests.postgres.settings import PgSafetyError


def _check_select_one(pg_test_engine) -> None:
    error_type = None
    try:
        with pg_test_engine.connect() as connection:
            result = connection.scalar(text("SELECT 1"))
    except (SQLAlchemyError, ValidationError, PgSafetyError) as exc:
        error_type = type(exc).__name__
    if error_type is not None:
        pytest.fail(
            f"PostgreSQL SELECT 1 failed ({error_type}); "
            "check the approved isolated test environment. Details suppressed.",
            pytrace=False,
        )
    assert result == 1


@pytest.mark.integration
def test_real_postgresql_select_one(pg_test_engine) -> None:
    from tests.postgres.engine import require_test_engine
    require_test_engine(pg_test_engine)
    _check_select_one(pg_test_engine)
