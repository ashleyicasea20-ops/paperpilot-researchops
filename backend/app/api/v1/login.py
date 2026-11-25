"""Login and current-user endpoints with authentication-specific error details."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.dependencies import JwtSettings, TokenSubject
from app.api.protected_route import ProtectedRoute, protected_error_response
from app.core.token import ACCESS_TOKEN_SECONDS, InvalidAccessToken, issue_access_token
from app.db.session import auth_read_session
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.services.authentication import InvalidCredentials, authenticate_user, get_active_user


class AuthenticationRoute(ProtectedRoute):
    invalid_input_detail = "Invalid login input"
    failure_detail = "Authentication failed"

    def error_response(self, error: Exception) -> JSONResponse:
        if isinstance(error, InvalidCredentials):
            return protected_error_response(401, "Invalid credentials")
        return super().error_response(error)


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=AuthenticationRoute)
SERVICE_ERRORS = {
    503: {"description": "Database or authentication configuration unavailable"},
    500: {"description": "Authentication failed"},
}


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        401: {"description": "Invalid credentials"},
        422: {"description": "Invalid login input"},
        **SERVICE_ERRORS,
    },
)
def login(request: LoginRequest, settings: JwtSettings) -> TokenResponse:
    user_id = authenticate_user(request)
    return TokenResponse(access_token=issue_access_token(user_id, settings), expires_in=ACCESS_TOKEN_SECONDS)


@router.get(
    "/me",
    response_model=UserResponse,
    responses={401: {"description": "Invalid access token"}, **SERVICE_ERRORS},
)
def me(user_id: TokenSubject) -> UserResponse:
    with auth_read_session() as session:
        user = get_active_user(session, user_id)
        if user is None:
            raise InvalidAccessToken
        return UserResponse.model_validate(user)
