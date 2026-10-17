"""Contas a pagar/receber, aging e conciliação com o extrato."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile

from finance_engine.analysis.reconciliation import MatchSuggestion
from finance_engine.api.deps import CurrentClient, DbSession, StaffOnly
from finance_engine.api.routers.imports import _read_limited
from finance_engine.api.schemas import (
    AgingOut,
    ApplyMatchesIn,
    ApplyMatchesOut,
    MatchOut,
    TitleImportOut,
    TitleRead,
)
from finance_engine.core.config import get_settings
from finance_engine.models import FinancialTitle, MatchMethod, TitleKind
from finance_engine.services.titles_service import TitleImportResult, TitlesService

router = APIRouter(tags=["Contas a pagar e receber"])
KindParam = Annotated[TitleKind, Query(description="RECEIVABLE (a receber) ou PAYABLE (a pagar)")]


@router.post("/clients/{client_id}/titles/import", response_model=TitleImportOut, status_code=201)
async def import_titles(
    db: DbSession,
    client: CurrentClient,
    file: Annotated[
        UploadFile,
        File(
            description=(
                "CSV/XLSX: Cliente ou Fornecedor, Documento, Emissão, Vencimento, "
                "Valor, Saldo, Pagamento"
            )
        ),
    ],
    kind: Annotated[TitleKind, Form()] = TitleKind.RECEIVABLE,
) -> TitleImportResult:
    """Reimportar a mesma planilha atualiza os saldos (não duplica)."""
    content = await _read_limited(file, get_settings().max_upload_bytes)
    return TitlesService(db).import_file(
        client.organization_id, client.id, content, receivable=kind is TitleKind.RECEIVABLE
    )


@router.get("/clients/{client_id}/titles", response_model=list[TitleRead])
def list_titles(
    db: DbSession, client: CurrentClient, kind: KindParam | None = None, open_only: bool = True
) -> list[FinancialTitle]:
    svc = TitlesService(db)
    if open_only:
        return svc.open_titles(client.id, kind)
    from sqlalchemy import select

    q = select(FinancialTitle).where(FinancialTitle.client_id == client.id)
    if kind:
        q = q.where(FinancialTitle.kind == kind)
    return list(db.scalars(q.order_by(FinancialTitle.due_date)))


@router.get("/clients/{client_id}/titles/aging", response_model=AgingOut)
def aging(
    db: DbSession,
    client: CurrentClient,
    kind: KindParam = TitleKind.RECEIVABLE,
    reference_date: date | None = None,
) -> object:
    return TitlesService(db).aging(client.id, kind, reference_date or date.today())


@router.get("/clients/{client_id}/reconciliation/suggestions", response_model=list[MatchOut])
def suggestions(
    db: DbSession, client: CurrentClient, today: date | None = None
) -> list[MatchSuggestion]:
    """Casamentos sugeridos extrato × títulos, com pontuação e motivos (``auto`` = confiável)."""
    return TitlesService(db).suggest(client.id, today or date.today())


@router.post("/clients/{client_id}/reconciliation/apply", response_model=ApplyMatchesOut)
def apply_matches(
    payload: ApplyMatchesIn,
    db: DbSession,
    client: CurrentClient,
    _: StaffOnly,
    today: date | None = None,
) -> ApplyMatchesOut:
    svc = TitlesService(db)
    if payload.matches is None:
        chosen = [m for m in svc.suggest(client.id, today or date.today()) if m.auto]
        method = MatchMethod.AUTO
    else:
        chosen = [
            MatchSuggestion(
                m.transaction_id,
                tuple(m.title_ids),
                tuple(m.allocated),
                m.score,
                tuple(m.reasons),
                m.auto,
            )
            for m in payload.matches
        ]
        method = MatchMethod.MANUAL
    return ApplyMatchesOut(applied=svc.apply(client.organization_id, client.id, chosen, method))
