"""Synchronous SQLAlchemy engine and session lifecycle management."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
import logging

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import (
    DBAPIError, DisconnectionError, InterfaceError, OperationalError,
    SQLAlchemyError, TimeoutError,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)
REGISTRATION_COMMITTED = "paperpilot.registration_committed"


class DatabaseUnavailableError(RuntimeError):
    """Raised when database configuration or connectivity is unavailable."""


def is_database_unavailable(error: Exception) -> bool:
    return isinstance(error, (
        DatabaseUnavailableError, OperationalError, InterfaceError,
        DisconnectionError, TimeoutError,
    )) or (isinstance(error, DBAPIError) and error.connection_invalidated)


@lru_cache
def get_engine() -> Engine:
    """Create one process-wide synchronous PostgreSQL engine."""

    return create_engine(
        get_settings().database_url,
        hide_parameters=True,
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


@contextmanager
def auth_read_session() -> Generator[Session, None, None]:
    """Finish an authentication read before using its result."""
    session = get_session_factory()()
    operation_failed = False
    try:
        yield session
    except BaseException:
        operation_failed = True
        try:
            session.rollback()
        except Exception:
            logger.warning("Authentication session rollback failed")
        raise
    finally:
        try:
            session.close()
        except Exception:
            logger.warning("Authentication session close failed")
            try:
                session.invalidate()
            except Exception:
                logger.warning("Authentication session invalidation failed")
            if not operation_failed:
                raise


def get_db_session() -> Generator[Session, None, None]:
    """Yield a request-scoped session and always release its connection."""

    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        _close_session(session)


def _close_session(session: Session) -> None:
    registration_committed = session.info.pop(REGISTRATION_COMMITTED, False)
    try:
        session.close()
    except Exception:
        if registration_committed is not True:
            raise
    else:
        return

    # The response is already backed by an acknowledged commit. Invalidate any
    # connections still held by the session without changing that result.
    logger.warning("Session cleanup failed after registration commit")
    try:
        session.invalidate()
    except Exception:
        logger.warning("Session invalidation failed after registration commit")


def check_database_connection() -> None:
    """Verify PostgreSQL connectivity with a side-effect-free query."""

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, ValidationError) as exc:
        raise DatabaseUnavailableError("Database is unavailable") from exc
