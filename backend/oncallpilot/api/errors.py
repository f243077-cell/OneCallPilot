"""Domain errors and the error envelope, in one place (``CLAUDE.md`` §4.2, API-001).

Every error response on every route is ``{"error": {code, message, details}}``
with a code from architecture §9.8. Routers raise ``ApiError`` subclasses and
never build error JSON themselves.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from oncallpilot_contracts.api import ErrorBody, ErrorEnvelope
from oncallpilot_contracts.enums import ErrorCode
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    status_code = 500
    code: ErrorCode = "INTERNAL"
    message = "internal error"

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        self.details = details or {}


class UnauthorizedError(ApiError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "missing or invalid access token"


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"
    message = "not found"


class ValidationFailedError(ApiError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "invalid request"


def envelope(
    status_code: int, code: ErrorCode, message: str, details: dict[str, Any]
) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message[:500], details=details))
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


_HTTP_CODES: dict[int, ErrorCode] = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return envelope(exc.status_code, exc.code, exc.message, exc.details)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Location and message only: the offending input is never echoed back.
    errors = [
        {"loc": [str(part) for part in err.get("loc", ())], "msg": str(err.get("msg", ""))}
        for err in exc.errors()
    ]
    return envelope(422, "VALIDATION_ERROR", "invalid request", {"errors": errors})


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code >= 500:
        code: ErrorCode = "INTERNAL"
    else:
        code = _HTTP_CODES.get(exc.status_code, "VALIDATION_ERROR")
    return envelope(exc.status_code, code, str(exc.detail), {})


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "unhandled error",
        exc_info=exc,
        extra={"request_id": request.headers.get("x-request-id")},
    )
    return envelope(500, "INTERNAL", "internal error", {})


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
