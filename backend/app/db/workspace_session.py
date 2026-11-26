"""Workspace-only write transactions; independent of registration markers."""

from collections.abc import Generator
from contextlib import contextmanager
import logging

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.db import session as db

logger = logging.getLogger(__name__)


class WorkspaceCommitUnknownError(RuntimeError):
    """Commit was attempted but its outcome was not acknowledged."""


def _rollback(session: Session) -> None:
    try:
        session.rollback()
    except Exception:
        logger.warning("Workspace transaction rollback failed")


def _close(session: Session, connection: Connection | None) -> None:
    try:
        session.close()
        return
    except Exception:
        logger.warning("Workspace session close failed")
    try:
        session.invalidate()
        return
    except Exception:
        logger.warning("Workspace session invalidation failed")
    if connection is not None:
        # A retained lease allows bounded cleanup even if Session cleanup fails.
        try:
            connection.invalidate()
        except Exception:
            logger.warning("Workspace connection invalidation failed")
        try:
            connection.close()
        except Exception:
            logger.warning("Workspace connection close failed")


@contextmanager
def workspace_write_session() -> Generator[Session, None, None]:
    """Return success only after commit acknowledgement, then release resources.

    Explicit flush failures happen before commit is attempted. Once commit()
    starts, an exception cannot prove whether the physical commit or a later
    SQLAlchemy event failed, so its outcome is UNKNOWN regardless of its type.
    Rollback cannot resolve a lost acknowledgement or undo an applied commit.
    Cleanup faults never replace a primary error or an acknowledged success.
    """

    session = db.get_session_factory()()
    connection = None
    committing = False
    unknown = False
    try:
        session.begin()
        if session.get_bind().dialect.name == "postgresql":
            connection = session.connection(execution_options={"isolation_level": "READ COMMITTED"})
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            session.execute(text("SET LOCAL statement_timeout = '15s'"))
        else:
            connection = session.connection()
        yield session
        # Separate known pre-commit failures from the opaque commit/event phase.
        session.flush()
        committing = True
        session.commit()
    except BaseException as error:
        _rollback(session)
        if isinstance(error, Exception) and committing:
            unknown = True
            logger.warning("Workspace commit outcome unknown")
        else:
            raise
    finally:
        _close(session, connection)
    if unknown:
        # Outside the handler: no credential-bearing exception context escapes.
        raise WorkspaceCommitUnknownError("Workspace commit outcome is unknown")
