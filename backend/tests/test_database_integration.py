"""Integration test against the configured real PostgreSQL instance."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import BACKEND_DIR
from app.db.session import get_engine


@pytest.mark.integration
def test_real_postgresql_select_one() -> None:
    if not Path(BACKEND_DIR / ".env").is_file():
        pytest.skip("backend/.env is required for the real PostgreSQL test")

    error_type = None
    try:
        with get_engine().connect() as connection:
            result = connection.scalar(text("SELECT 1"))
    except (SQLAlchemyError, ValidationError) as exc:
        # Driver traceback frames may contain raw credentials. Do not report
        # the exception, its message, locals, or chained traceback through pytest.
        error_type = type(exc).__name__

    # Fail outside the exception handler so pytest has no sensitive context.
    if error_type is not None:
        pytest.fail(
            f"PostgreSQL SELECT 1 failed ({error_type}); "
            "check local server availability and configuration. Details suppressed.",
            pytrace=False,
        )
    assert result == 1
