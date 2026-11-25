"""Route-scoped, redacted responses for authentication and authorization."""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import Receive, Scope, Send

from app.api.request_errors import is_fastapi_body_parse_error
from app.core.config import AuthenticationConfigurationError, Settings
from app.core.token import InvalidAccessToken
from app.db.session import is_database_unavailable
from app.services.workspace_access import InsufficientWorkspacePermissions, WorkspaceNotFound

logger = logging.getLogger(__name__)


def protected_error_response(status: int, detail: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(status_code=status, content={"detail": detail}, headers=headers)


class ProtectedRoute(APIRoute):
    invalid_input_detail = "Invalid request input"
    failure_detail = "Request failed"

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return protected_error_response(422, self.invalid_input_detail)
            except HTTPException as error:
                if not is_fastapi_body_parse_error(error):
                    raise
                if isinstance(error.__cause__, (UnicodeDecodeError, RecursionError)):
                    return protected_error_response(422, self.invalid_input_detail)
                logger.error("Protected request parsing failed")
                return protected_error_response(500, self.failure_detail)

        return safe_handler

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        try:
            await super().handle(scope, receive, tracked_send)
        except HTTPException:
            raise
        except Exception as error:
            if started:
                raise RuntimeError("Protected response interrupted") from None
            await self.error_response(error)(scope, receive, send)

    def error_response(self, error: Exception) -> JSONResponse:
        if isinstance(error, InvalidAccessToken):
            return protected_error_response(401, "Invalid access token")
        if isinstance(error, WorkspaceNotFound):
            return protected_error_response(404, "Workspace not found")
        if isinstance(error, InsufficientWorkspacePermissions):
            return protected_error_response(403, "Insufficient workspace permissions")
        if isinstance(error, AuthenticationConfigurationError):
            logger.warning("Authentication configuration is unavailable")
            return protected_error_response(503, "Authentication is unavailable")
        if is_database_unavailable(error) or (
            isinstance(error, ValidationError) and error.title == Settings.__name__
        ):
            logger.warning("Protected request database is unavailable")
            return protected_error_response(503, "Database is unavailable")
        logger.error("Protected request failed")
        return protected_error_response(500, self.failure_detail)
