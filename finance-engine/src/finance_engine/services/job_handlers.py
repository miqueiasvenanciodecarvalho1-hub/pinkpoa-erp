"""Handlers dos jobs em segundo plano."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from finance_engine.core.storage import LocalStorage
from finance_engine.services.ingestion_service import IngestionService
from finance_engine.services.jobs import job_handler


@job_handler("import_statement_file")
def import_statement_file(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    from finance_engine.api.routers.imports import build_options

    content = LocalStorage().read(payload["storage_key"])
    options = build_options(**payload["options"])
    batch = IngestionService(session).process(
        uuid.UUID(payload["batch_id"]), content, options, raise_on_error=False
    )
    return {"status": str(batch.status), "imported": batch.imported_count}
