"""Login and current-user endpoints with a route-local error boundary."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.types import Receive, Scope, Send

from app.api.request_errors import is_fastapi_body_parse_error
from app.core.config import AuthenticationConfigurationError, AuthSettings, Settings, get_auth_settings
from app.core.token import ACCESS_TOKEN_SECONDS, InvalidAccessToken, issue_access_token, read_access_token
from app.db.session import auth_read_session, is_database_unavailable
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.services.authentication import InvalidCredentials, authenticate_user, get_active_user

logger = logging.getLogger(__name__)


def _error_response(status: int, detail: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(status_code=status, content={"detail": detail}, headers=headers)


class AuthenticationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return _error_response(422, "Invalid login input")
            except HTTPException as error:
                if not is_fastapi_body_parse_error(error):
                    raise
                if isinstance(error.__cause__, (UnicodeDecodeError, RecursionError)):
                    return _error_response(422, "Invalid login input")
                logger.error("Authentication request parsing failed")
                return _error_response(500, "Authentication failed")

        return safe_handler

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await super().handle(scope, receive, tracked_send)
        except HTTPException:
            raise
        except Exception as error:
            if started:
                raise RuntimeError("Authentication response interrupted") from None
            if isinstance(error, InvalidCredentials):
                response = _error_response(401, "Invalid credentials")
            elif isinstance(error, InvalidAccessToken):
                response = _error_response(401, "Invalid access token")
            elif isinstance(error, AuthenticationConfigurationError):
                logger.warning("Authentication configuration is unavailable")
                response = _error_response(503, "Authentication is unavailable")
            elif is_database_unavailable(error) or (
                isinstance(error, ValidationError) and error.title == Settings.__name__
            ):
                logger.warning("Authentication database is unavailable")
                response = _error_response(503, "Database is unavailable")
            else:
                logger.error("Authentication request failed")
                response = _error_response(500, "Authentication failed")
            await response(scope, receive, send)


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=AuthenticationRoute)
bearer = HTTPBearer(auto_error=False)
JwtSettings = Annotated[AuthSettings, Depends(get_auth_settings)]
SERVICE_ERRORS = {
    503: {"description": "Database or authentication configuration unavailable"},
    500: {"description": "Authentication failed"},
}


def _require_bearer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or "," in credentials.credentials:
        raise InvalidAccessToken
    if sum(name.lower() == b"authorization" for name, _ in request.headers.raw) != 1:
        raise InvalidAccessToken
    return credentials.credentials


def _get_token_subject(token: Annotated[str, Depends(_require_bearer)], settings: JwtSettings) -> UUID:
    return read_access_token(token, settings)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        401: {"description": "Invalid credentials"},
        422: {"description": "Invalid login input"},
        **SERVICE_ERRORS,
    },
)
def login(request: LoginRequest, response: Response, settings: JwtSettings) -> TokenResponse:
    user_id = authenticate_user(request)
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(access_token=issue_access_token(user_id, settings), expires_in=ACCESS_TOKEN_SECONDS)


@router.get(
    "/me",
    response_model=UserResponse,
    responses={401: {"description": "Invalid access token"}, **SERVICE_ERRORS},
)
def me(
    response: Response, user_id: Annotated[UUID, Depends(_get_token_subject)],
) -> UserResponse:
    with auth_read_session() as session:
        user = get_active_user(session, user_id)
        if user is None:
            raise InvalidAccessToken
        public_user = UserResponse.model_validate(user)
    response.headers["Cache-Control"] = "no-store"
    return public_user
