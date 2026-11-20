"""Password policy and reusable Argon2id hashing; no authentication endpoints."""

from pwdlib import PasswordHash

MIN_PASSWORD_CHARACTERS = 15
MAX_PASSWORD_CHARACTERS = 128
MAX_PASSWORD_BYTES = 1024

_password_hash = PasswordHash.recommended()


def validate_password(password: str) -> str:
    """Validate without trimming or normalizing a password, before hashing."""
    if not isinstance(password, str):
        raise ValueError("Invalid password")
    try:
        byte_count = len(password.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("Invalid password") from None
    if byte_count > MAX_PASSWORD_BYTES or not (
        MIN_PASSWORD_CHARACTERS <= len(password) <= MAX_PASSWORD_CHARACTERS
    ):
        raise ValueError("Invalid password")
    return password


def hash_password(password: str) -> str:
    """Use the library's Argon2id implementation and random salt."""
    return _password_hash.hash(validate_password(password))


def verify_password(password: str, encoded_hash: str) -> bool:
    """Reusable verification primitive; this does not create a login session."""
    return _password_hash.verify(password, encoded_hash)
