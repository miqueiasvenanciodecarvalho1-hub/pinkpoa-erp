"""Tradução de exceções de domínio para respostas HTTP."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from finance_engine.core.exceptions import (
    ConflictError,
    DuplicateImportError,
    FinanceEngineError,
    NotFoundError,
    ParseError,
    UnsupportedFormatError,
)

_STATUS: list[tuple[type[FinanceEngineError], int]] = [
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (UnsupportedFormatError, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
    (ParseError, status.HTTP_422_UNPROCESSABLE_CONTENT),
]


async def _handle(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, FinanceEngineError)
    code = next((c for t, c in _STATUS if isinstance(exc, t)), status.HTTP_400_BAD_REQUEST)
    body: dict[str, object] = {"detail": str(exc), "error": type(exc).__name__}
    if isinstance(exc, DuplicateImportError):
        body["existing_batch_id"] = exc.existing_batch_id
    batch_id = getattr(exc, "batch_id", None)
    if batch_id:
        body["batch_id"] = batch_id
    return JSONResponse(status_code=code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(FinanceEngineError, _handle)
