import logging
from http import HTTPStatus

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed", details=details)


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_").replace("-", "_")
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("Database unavailable: %r", exc)
    return error_response(
        503, "service_unavailable", "The service is temporarily unavailable, please retry"
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(500, "internal_error", "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    # Also covers psycopg_pool.PoolTimeout, deadlocks and serialization failures.
    app.add_exception_handler(psycopg.OperationalError, database_unavailable_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
