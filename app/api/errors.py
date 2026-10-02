"""Map errors to the status / errorMessage contract."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import AppError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.info(
            "request_rejected path=%s status_code=%s message=%s",
            request.url.path,
            exc.status_code,
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": exc.status, "errorMessage": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "errorMessage": "Invalid request"},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"status": "ERROR", "errorMessage": "Internal error"},
        )
