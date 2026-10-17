"""Simuladores: antecipação de recebíveis."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status

from finance_engine.analysis.anticipation import (
    AnticipationResult,
    AnticipationTerms,
    Receivable,
    simulate_anticipation,
)
from finance_engine.api.deps import CurrentClient, CurrentPrincipal, DbSession
from finance_engine.api.schemas import AnticipationIn, AnticipationOut
from finance_engine.models import FinancialTitle, TitleKind

router = APIRouter(tags=["Simuladores"])
SIMPLES_IOF_DAILY = Decimal("0.00001367")


def _terms(p: AnticipationIn) -> AnticipationTerms:
    return AnticipationTerms(
        monthly_rate=p.monthly_rate,
        operation_date=p.operation_date or date.today(),
        float_days=p.float_days,
        fee_per_title=p.fee_per_title,
        fixed_fee=p.fixed_fee,
        iof=p.iof,
        iof_daily_rate=SIMPLES_IOF_DAILY if p.simples_nacional else Decimal("0.000041"),
        alternative_monthly_rate=p.alternative_monthly_rate,
    )


def _run(items: list[Receivable], p: AnticipationIn) -> AnticipationResult:
    try:
        return simulate_anticipation(items, _terms(p))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/tools/anticipation", response_model=AnticipationOut)
def simulate(payload: AnticipationIn, _: CurrentPrincipal) -> AnticipationResult:
    """Simulação avulsa: informe os recebíveis (valor e vencimento) e as condições da operação."""
    if not payload.receivables:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "informe os recebíveis")
    return _run(
        [Receivable(r.amount, r.due_date, r.reference) for r in payload.receivables], payload
    )


@router.post("/clients/{client_id}/anticipation", response_model=AnticipationOut)
def simulate_client(
    payload: AnticipationIn, db: DbSession, client: CurrentClient
) -> AnticipationResult:
    """Simula a antecipação dos títulos a receber escolhidos (ou de todos os a vencer)."""
    from finance_engine.services.titles_service import TitlesService

    titles = [
        t
        for t in TitlesService(db).open_titles(client.id, TitleKind.RECEIVABLE)
        if not payload.title_ids or t.id in set(payload.title_ids)
    ]
    items = [
        Receivable(t.open_amount, t.due_date, f"{t.counterparty} {t.document_number or ''}".strip())
        for t in titles
    ]
    if payload.receivables:
        items += [Receivable(r.amount, r.due_date, r.reference) for r in payload.receivables]
    if not items:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "nenhum título a receber em aberto"
        )
    _ = FinancialTitle  # tipagem
    return _run(items, payload)
