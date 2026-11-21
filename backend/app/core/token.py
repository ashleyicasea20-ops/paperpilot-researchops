"""Issue and validate the API's short-lived access tokens."""

from datetime import datetime, timezone
import re
from uuid import UUID

import jwt

from app.core.config import AuthSettings

ACCESS_TOKEN_SECONDS = 900
ISSUER = "paperpilot-researchops"
AUDIENCE = "paperpilot-api"
ALGORITHM = "HS256"
MAX_TOKEN_LENGTH = 4096
TIME_CLAIMS = ("iat", "nbf", "exp")
REQUIRED_CLAIMS = ["sub", *TIME_CLAIMS, "iss", "aud", "token_type"]
JWT_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class InvalidAccessToken(Exception):
    """The bearer credential cannot authenticate a user."""


def issue_access_token(user_id: UUID, settings: AuthSettings) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "nbf": now,
            "exp": now + ACCESS_TOKEN_SECONDS,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "token_type": "access",
        },
        bytes.fromhex(settings.jwt_secret_key.get_secret_value()),
        algorithm=ALGORITHM,
    )


def read_access_token(token: str, settings: AuthSettings) -> UUID:
    if len(token) > MAX_TOKEN_LENGTH or not JWT_PATTERN.fullmatch(token):
        raise InvalidAccessToken

    key = bytes.fromhex(settings.jwt_secret_key.get_secret_value())
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": REQUIRED_CLAIMS, "strict_aud": True},
        )
        if claims["token_type"] != "access" or any(type(claims[name]) is not int for name in TIME_CLAIMS):
            raise InvalidAccessToken
        if not claims["iat"] <= claims["nbf"] < claims["exp"] <= claims["iat"] + ACCESS_TOKEN_SECONDS:
            raise InvalidAccessToken
        return UUID(claims["sub"])
    except (jwt.InvalidTokenError, TypeError, ValueError, OverflowError):
        raise InvalidAccessToken from None
