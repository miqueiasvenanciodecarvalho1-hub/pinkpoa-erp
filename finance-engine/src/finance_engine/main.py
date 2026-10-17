"""Aplicação FastAPI. Rodar com: ``uvicorn finance_engine.main:app --reload``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from finance_engine.api.errors import register_exception_handlers
from finance_engine.api.routers import analytics, classification, imports, tenancy, transactions
from finance_engine.core.config import get_settings
from finance_engine.db.base import Base
from finance_engine.db.session import get_engine

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if get_settings().auto_create_schema:
        import finance_engine.models  # noqa: F401 - registra os modelos no metadata

        Base.metadata.create_all(get_engine())
    yield


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    app = FastAPI(
        title="Finance Engine — Ingestão e Análise Financeira",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    for module in (tenancy, imports, transactions, classification, analytics):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/health", tags=["Infra"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
