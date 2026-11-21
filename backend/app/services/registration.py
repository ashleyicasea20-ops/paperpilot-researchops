"""Registration transaction and precise, non-sensitive error classification."""

import sqlite3

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import is_database_unavailable
from app.models import User
from app.schemas.auth import RegisterRequest, UserResponse

EMAIL_INDEX = "uq_users_email_lower"


class EmailAlreadyRegistered(Exception):
    """The database rejected a duplicate email identity."""


class RegistrationUnavailable(Exception):
    """Database connectivity is unavailable."""


class RegistrationFailed(Exception):
    """An unexpected internal registration failure."""


def is_email_conflict(error: IntegrityError, dialect: str) -> bool:
    original = error.orig
    if dialect == "postgresql":
        return (
            getattr(original, "sqlstate", None) == "23505"
            and getattr(getattr(original, "diag", None), "constraint_name", None) == EMAIL_INDEX
        )
    if dialect == "sqlite" and isinstance(original, sqlite3.IntegrityError):
        return (
            getattr(original, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
            and str(original) == f"UNIQUE constraint failed: index '{EMAIL_INDEX}'"
        )
    return False


def register_user(session: Session, request: RegisterRequest) -> UserResponse:
    """Let the unique index arbitrate duplicates, including concurrent inserts.

    Flush first to load database defaults and build the public result before
    commit, so response validation failure cannot commit an incomplete result.
    Session ownership/closing remains with the existing request dependency.
    """
    failure = RegistrationFailed()
    dialect = None
    try:
        dialect = session.get_bind().dialect.name
        user = User(email=str(request.email), password_hash=hash_password(request.password.get_secret_value()))
        session.add(user)
        session.flush()
        response = UserResponse.model_validate(user)
        session.commit()
        return response
    except Exception as exc:
        if isinstance(exc, IntegrityError):
            if is_email_conflict(exc, dialect):
                failure = EmailAlreadyRegistered()
        elif is_database_unavailable(exc):
            failure = RegistrationUnavailable()
        try:
            session.rollback()
        except Exception:
            # A broken connection may also make rollback fail. The dependency
            # still closes the session; never expose either raw exception.
            pass
    # Outside the handler, so the original exception has no chained context.
    raise failure from None
