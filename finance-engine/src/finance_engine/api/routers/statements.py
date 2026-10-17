"""Demonstrativos contábeis (balanço, DRE, balancete) e indicadores de capital de giro."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from finance_engine.ai import get_ai_provider
from finance_engine.analysis.indicators import IndicatorAssumptions
from finance_engine.api.deps import CurrentClient, DbSession, StaffOnly
from finance_engine.api.routers.imports import _read_limited
from finance_engine.api.schemas import (
    IndicatorsOut,
    IndicatorsReportOut,
    StatementRead,
    StatementUpdate,
)
from finance_engine.core.config import get_settings
from finance_engine.ingestion.statements import AIStatementExtractor
from finance_engine.models import FinancialStatement
from finance_engine.services.statements_service import StatementsService
from finance_engine.services.titles_service import TitlesService

router = APIRouter(tags=["Demonstrativos e indicadores"])


def _service(db: DbSession, want_ai: bool) -> StatementsService:
    provider = get_ai_provider() if want_ai else None
    return StatementsService(db, AIStatementExtractor(provider) if provider else None)


@router.post("/clients/{client_id}/statements", response_model=list[StatementRead], status_code=201)
async def upload_statement(
    db: DbSession,
    client: CurrentClient,
    file: Annotated[UploadFile, File(description="SPED ECD (.txt), planilha (CSV/XLSX) ou PDF")],
    use_ai: Annotated[
        bool, Form(description="Usar IA (Claude, ChatGPT ou Gemini, conforme FIN_AI_PROVIDER)")
    ] = False,
) -> list[FinancialStatement]:
    """Gera rascunhos já mapeados para o plano padrão; o consultor revisa e valida."""
    content = await _read_limited(file, get_settings().max_upload_bytes)
    return _service(db, use_ai).upload(
        client.organization_id, client.id, content, file.filename or "demonstrativo", use_ai=use_ai
    )


@router.get("/clients/{client_id}/statements", response_model=list[StatementRead])
def list_statements(db: DbSession, client: CurrentClient) -> list[FinancialStatement]:
    from sqlalchemy import select

    return list(
        db.scalars(
            select(FinancialStatement)
            .where(FinancialStatement.client_id == client.id)
            .order_by(FinancialStatement.period_end.desc().nulls_last())
        )
    )


@router.patch("/clients/{client_id}/statements/{statement_id}", response_model=StatementRead)
def update_statement(
    statement_id: uuid.UUID,
    payload: StatementUpdate,
    db: DbSession,
    client: CurrentClient,
    _: StaffOnly,
) -> FinancialStatement:
    svc = StatementsService(db)
    return svc.update_values(
        svc.get(client.id, statement_id), payload.values, payload.validate_statement
    )


@router.get("/clients/{client_id}/analytics/indicators", response_model=IndicatorsReportOut)
def indicators(
    db: DbSession,
    client: CurrentClient,
    days: int = 360,
    variable_selling_share: Decimal = Decimal("0.6"),
    today: date | None = None,
) -> IndicatorsReportOut:
    """PMR, PMP, PME, ciclos, NCG, saldo de tesouraria, liquidez, margens e ponto de equilíbrio.

    Os prazos "operacionais" vêm dos títulos quitados (realidade do dia a dia); os "contábeis",
    do último balanço/DRE (validado, ou o rascunho mais recente)."""
    result, used = StatementsService(db).indicators(
        client.id, IndicatorAssumptions(days=days, variable_selling_share=variable_selling_share)
    )
    ops = TitlesService(db).operational_terms(client.id, today or date.today())
    return IndicatorsReportOut(
        accounting=IndicatorsOut.model_validate(result),
        operational_pmr=ops["pmr"],
        operational_pmp=ops["pmp"],
        statements=[StatementRead.model_validate(s) for s in used],
    )
