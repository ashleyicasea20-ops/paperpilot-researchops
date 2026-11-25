"""Read existing users and verify credentials without database writes."""

from dataclasses import dataclass
import logging
from uuid import UUID

from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import error_to_str, lib
from pwdlib.exceptions import HashVerificationError, UnknownHashError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.core.token import InvalidAccessToken
from app.db.session import auth_read_session
from app.models import User
from app.schemas.auth import LoginRequest

logger = logging.getLogger(__name__)

# Generated once with PasswordHash.recommended() for a discarded random value.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$/fu84bGj9FNo5anPJjfNYw$"
    "+4vKvL7jG3nP6NjvdILyLm6KrJIoNcjVWsRxuuvMNhg"
)

# pwdlib wraps both malformed encodings and runtime faults. Argon2 exposes the
# native reason as a message, so match only its documented data errors.
INVALID_ARGON2_ENCODING_ERRORS = frozenset(error_to_str(code) for code in (
    lib.ARGON2_DECODING_FAIL,
    lib.ARGON2_DECODING_LENGTH_FAIL,
    lib.ARGON2_OUTPUT_TOO_SHORT,
    lib.ARGON2_OUTPUT_TOO_LONG,
    lib.ARGON2_SALT_TOO_SHORT,
    lib.ARGON2_SALT_TOO_LONG,
))


class InvalidCredentials(Exception):
    """The supplied identity cannot authenticate an active user."""


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    user_id: UUID


def _verify_stored_password(password: str, encoded_hash: str) -> bool:
    try:
        return verify_password(password, encoded_hash)
    except UnknownHashError:
        pass
    except HashVerificationError as error:
        cause = error.__cause__
        if not (isinstance(cause, InvalidHashError) or (
            isinstance(cause, VerificationError) and str(cause) in INVALID_ARGON2_ENCODING_ERRORS
        )):
            raise

    logger.warning("Stored password hash could not be verified")
    verify_password(password, DUMMY_PASSWORD_HASH)
    return False


def authenticate_user(request: LoginRequest) -> UUID:
    with auth_read_session() as session:
        credentials = session.execute(
            select(User.id, User.is_active, User.password_hash)
            .where(func.lower(User.email) == request.email)
        ).one_or_none()

    password = request.password.get_secret_value()
    if credentials is None or not credentials.is_active:
        verify_password(password, DUMMY_PASSWORD_HASH)
        raise InvalidCredentials
    if not _verify_stored_password(password, credentials.password_hash):
        raise InvalidCredentials
    return credentials.id


def _active_account(user_id: UUID):
    return User.id == user_id, User.is_active.is_(True)


def require_active_identity(session: Session, subject: UUID) -> AuthenticatedIdentity:
    with session.no_autoflush:
        user_id = session.scalar(select(User.id).where(*_active_account(subject)))
    if user_id is None:
        raise InvalidAccessToken
    return AuthenticatedIdentity(user_id)


def get_active_user(session: Session, user_id: UUID) -> User | None:
    return session.scalar(select(User).where(*_active_account(user_id)))
