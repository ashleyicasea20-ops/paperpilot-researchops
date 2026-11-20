"""Registration endpoint with a boundary covering validation and dependencies."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi import routing as fastapi_routing
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from app.db.session import REGISTRATION_COMMITTED, get_db_session
from app.schemas.auth import RegisterRequest, UserResponse
from app.services.registration import (
    EmailAlreadyRegistered, RegistrationFailed, RegistrationUnavailable,
    is_database_unavailable, register_user,
)


def _is_fastapi_body_parse_error(error: HTTPException) -> bool:
    if (
        type(error) is not HTTPException
        or error.status_code != 400
        or error.detail != "There was an error parsing the body"
        or error.__cause__ is None
    ):
        return False

    # FastAPI uses a plain HTTPException here. Verify where it was raised so
    # explicit HTTP errors with the same status, detail and cause pass through.
    origin = error.__traceback__
    if origin is None:
        return False
    while origin.tb_next is not None:
        origin = origin.tb_next
    return origin.tb_frame.f_globals is vars(fastapi_routing)


class RegistrationRoute(APIRoute):
    """Never echo validation inputs/ctx/msg or leak dependency exceptions.

    Returning a response here prevents sensitive errors reaching the server's
    unhandled-error logger. Other routes retain their existing error behavior.
    """

    async def handle(self, scope, receive, send):
        # FastAPI closes even function-scoped dependencies outside the inner
        # request handler. Protect that cleanup path as well as the endpoint.
        started = False
        failure_response = None

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await super().handle(scope, receive, tracked_send)
        except Exception as exc:
            unavailable = isinstance(exc, ValidationError) or is_database_unavailable(exc)
            failure_response = JSONResponse(
                status_code=503 if unavailable else 500,
                content={"detail": "Database is unavailable" if unavailable else "Registration failed"},
            )
        if failure_response is not None:
            if started:
                # Do not expose the original exception if transport failed after
                # headers. Sending a second response would violate ASGI.
                raise RuntimeError("Registration response interrupted") from None
            await failure_response(scope, receive, send)

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                code, detail = 422, "Invalid registration input"
            except HTTPException as exc:
                if not _is_fastapi_body_parse_error(exc):
                    raise
                cause = exc.__cause__
                if isinstance(cause, UnicodeDecodeError):
                    code, detail = 422, "Invalid registration input"
                elif isinstance(cause, RuntimeError) and not isinstance(cause, RecursionError):
                    code, detail = 500, "Registration failed"
                else:
                    raise
            except EmailAlreadyRegistered:
                code, detail = 409, "Email is already registered"
            except RegistrationUnavailable:
                code, detail = 503, "Database is unavailable"
            except RegistrationFailed:
                code, detail = 500, "Registration failed"
            except Exception as exc:
                # Includes Settings validation and session creation before yield.
                if isinstance(exc, ValidationError) or is_database_unavailable(exc):
                    code, detail = 503, "Database is unavailable"
                else:
                    code, detail = 500, "Registration failed"
            return JSONResponse(status_code=code, content={"detail": detail})

        return safe_handler


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=RegistrationRoute)


@router.post(
    "/register", status_code=201, response_model=UserResponse,
    responses={409: {"description": "Email is already registered"},
               422: {"description": "Invalid registration input"},
               503: {"description": "Database is unavailable"},
               500: {"description": "Registration failed"}},
)
def register(
    request: RegisterRequest,
    session: Annotated[Session, Depends(get_db_session, scope="function")],
) -> UserResponse:
    response = register_user(session, request)
    session.info[REGISTRATION_COMMITTED] = True
    return response
