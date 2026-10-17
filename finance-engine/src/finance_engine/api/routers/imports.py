"""Upload de extratos e arquivos bancários (OFX, CSV, XLSX, CNAB 240/400)."""

from __future__ import annotations

import json
import logging
import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Path,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance_engine.api.deps import CurrentClient, CurrentOrg, DbSession, SessionFactory
from finance_engine.api.schemas import ColumnMappingIn, ImportBatchRead
from finance_engine.core.config import get_settings
from finance_engine.ingestion import ParserOptions, TabularOptions
from finance_engine.models import ImportBatch, SourceFormat
from finance_engine.services.ingestion_service import ImportRequest, IngestionService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Importações"])

_CHUNK = 1024 * 1024


class ProcessingMode(StrEnum):
    SYNC = "sync"
    BACKGROUND = "background"


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """Lê o upload em blocos, abortando cedo se exceder o limite (não carrega 1 GB na memória)."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(_CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"arquivo excede {limit // (1024 * 1024)} MB"
            )
        chunks.append(chunk)
    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "arquivo vazio")
    return b"".join(chunks)


def _build_options(
    column_mapping: str | None,
    sheet_name: str | None,
    header_row: int | None,
    date_format: str | None,
    invert_sign: bool,
) -> ParserOptions:
    mapping = ColumnMappingIn()
    if column_mapping:
        try:
            mapping = ColumnMappingIn.model_validate(json.loads(column_mapping))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"column_mapping inválido: {exc}"
            ) from exc
    sheet: str | int = 0
    if sheet_name:
        sheet = int(sheet_name) if sheet_name.isdigit() else sheet_name
    return ParserOptions(
        tabular=TabularOptions(
            mapping=mapping.to_domain(),
            sheet_name=sheet,
            header_row=header_row,
            date_format=date_format,
            invert_sign=invert_sign,
        ),
        max_invalid_ratio=get_settings().max_invalid_row_ratio,
    )


def run_import_job(
    factory: sessionmaker[Session], batch_id: uuid.UUID, content: bytes, options: ParserOptions
) -> None:
    """Executado fora do request: abre a própria sessão (a do request já foi fechada)."""
    session = factory()
    try:
        IngestionService(session).process(batch_id, content, options, raise_on_error=False)
    except Exception:  # o lote já foi marcado como FAILED; aqui só registramos
        logger.exception("job de importação %s falhou", batch_id)
    finally:
        session.close()


@router.post(
    "/clients/{client_id}/bank-accounts/{bank_account_id}/imports",
    response_model=ImportBatchRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        202: {"description": "Aceito para processamento em background"},
        409: {"description": "Arquivo já importado nesta conta"},
        415: {"description": "Formato não suportado"},
        422: {"description": "Arquivo inválido / mapeamento de colunas incorreto"},
    },
)
async def upload_statement(
    response: Response,
    background_tasks: BackgroundTasks,
    db: DbSession,
    factory: SessionFactory,
    client: CurrentClient,
    bank_account_id: Annotated[uuid.UUID, Path()],
    file: Annotated[UploadFile, File(description="OFX, CSV, XLSX ou CNAB 240/400 (retorno)")],
    source_format: Annotated[SourceFormat | None, Form()] = None,
    mode: Annotated[ProcessingMode, Form()] = ProcessingMode.SYNC,
    column_mapping: Annotated[
        str | None,
        Form(
            description='JSON, ex.: {"date": "Dt. Mov", "description": ["Histórico"], '
            '"amount": "Valor R$"}'
        ),
    ] = None,
    sheet_name: Annotated[str | None, Form()] = None,
    header_row: Annotated[int | None, Form(ge=0)] = None,
    date_format: Annotated[str | None, Form(examples=["%d/%m/%Y"])] = None,
    invert_sign: Annotated[bool, Form()] = False,
) -> ImportBatch:
    content = await _read_limited(file, get_settings().max_upload_bytes)
    options = _build_options(column_mapping, sheet_name, header_row, date_format, invert_sign)
    service = IngestionService(db)
    batch = service.register(
        ImportRequest(
            organization_id=client.organization_id,
            client_id=client.id,
            bank_account_id=bank_account_id,
            filename=file.filename or "upload",
            content=content,
            source_format=source_format,
            options=options,
        )
    )
    db.commit()

    if mode is ProcessingMode.BACKGROUND:
        # BackgroundTasks basta para volumes modestos; em escala, enfileire ``batch.id`` num
        # worker (Celery/ARQ) com o arquivo em object storage (S3/GCS) — o service é o mesmo.
        background_tasks.add_task(run_import_job, factory, batch.id, content, options)
        response.status_code = status.HTTP_202_ACCEPTED
        return batch

    return service.process(batch.id, content, options)


@router.get("/imports/{batch_id}", response_model=ImportBatchRead)
def get_import(batch_id: uuid.UUID, db: DbSession, org: CurrentOrg) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.organization_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "importação não encontrada")
    return batch


@router.get("/clients/{client_id}/imports", response_model=list[ImportBatchRead])
def list_imports(db: DbSession, client: CurrentClient) -> list[ImportBatch]:
    return list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.client_id == client.id)
            .order_by(ImportBatch.created_at.desc())
            .limit(100)
        )
    )
