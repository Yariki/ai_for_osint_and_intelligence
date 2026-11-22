"""One error shape for every failure. No internals reach the client."""

from collections.abc import Mapping

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ndm.api.schemas.common import ErrorBody, ErrorDetail, ErrorResponse

log = structlog.get_logger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class ApiError(Exception):
    """Raise from an endpoint or a dependency to answer with the error shape."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers


def _response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or []))
    return JSONResponse(body.model_dump(), status_code=status_code, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_request: Request, error: ApiError) -> JSONResponse:
        return _response(error.status_code, error.code, error.message, error.details, error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        # Only the location and the message are passed on. The rejected input
        # is left out: it may be a password.
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in item["loc"][1:]) or None,
                code=item["type"],
                message=item["msg"],
            )
            for item in error.errors()
        ]
        return _response(422, "validation_error", "The request is not valid.", details)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(error.status_code, "http_error")
        return _response(error.status_code, code, str(error.detail), headers=error.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        log.error("unhandled_error", path=request.url.path, exc_info=error)
        return _response(500, "internal_error", "An internal error occurred.")
