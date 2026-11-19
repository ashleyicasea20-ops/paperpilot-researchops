"""Integration test against the configured real PostgreSQL instance."""

from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import BACKEND_DIR
from app.db.session import get_engine


@pytest.mark.integration
def test_real_postgresql_select_one() -> None:
    if not Path(BACKEND_DIR / ".env").is_file():
        pytest.skip("backend/.env is required for the real PostgreSQL test")

    with get_engine().connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
