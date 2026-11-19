"""Synchronous SQLAlchemy engine and session lifecycle management."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


class DatabaseUnavailableError(RuntimeError):
    """Raised when database configuration or connectivity is unavailable."""


@lru_cache
def get_engine() -> Engine:
    """Create one process-wide synchronous PostgreSQL engine."""

    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=5,
        pool_recycle=1800,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Create the application session factory."""

    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    """Yield a request-scoped session and always release its connection."""

    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> None:
    """Verify PostgreSQL connectivity with a side-effect-free query."""

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, ValidationError) as exc:
        raise DatabaseUnavailableError("Database is unavailable") from exc
