"""Recognize request parsing failures wrapped by FastAPI."""

from fastapi import routing as fastapi_routing
from starlette.exceptions import HTTPException


def is_fastapi_body_parse_error(error: HTTPException) -> bool:
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
