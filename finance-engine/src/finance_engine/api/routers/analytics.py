from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from finance_engine.analysis.forecast import Scenario
from finance_engine.analysis.statements import Granularity
from finance_engine.api.deps import CurrentClient, DbSession
from finance_engine.api.schemas import CashFlowReportOut, CashFlowStatementOut
from finance_engine.services.analytics_service import AnalyticsService, ForecastOptions

router = APIRouter(tags=["Análises"])

MAX_RANGE_DAYS = 3 * 366

AccountFilter = Annotated[list[uuid.UUID] | None, Query(alias="bank_account_id")]
Today = Annotated[
    date | None, Query(description="Data de corte realizado/projetado (padrão: hoje)")
]
ScenarioParam = Annotated[
    Scenario, Query(description="Cenário aplicado aos valores previstos por recorrência")
]


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "end deve ser >= start")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "período máximo de 3 anos")


@router.get("/clients/{client_id}/analytics/cash-flow", response_model=CashFlowReportOut)
def cash_flow(
    db: DbSession,
    client: CurrentClient,
    start: date,
    end: date,
    bank_account_ids: AccountFilter = None,
    low_balance_threshold: Decimal | None = None,
    top_n: Annotated[int, Query(ge=1, le=50)] = 10,
    today: Today = None,
    forecast: bool = True,
    scenario: ScenarioParam = Scenario.REALISTIC,
) -> CashFlowReportOut:
    """Fluxo de caixa diário (realizado + projetado), saldo acumulado, maiores despesas,
    padrões recorrentes detectados e alertas (saldo negativo projetado, picos de saída...)."""
    _validate_range(start, end)
    report = AnalyticsService(db).cash_flow_report(
        client.organization_id,
        client.id,
        start,
        end,
        bank_account_ids=bank_account_ids,
        today=today,
        top_n=top_n,
        low_balance_threshold=low_balance_threshold,
        forecast=ForecastOptions(enabled=forecast, scenario=scenario),
    )
    return CashFlowReportOut.model_validate(report)


@router.get(
    "/clients/{client_id}/analytics/cash-flow-statement", response_model=CashFlowStatementOut
)
def cash_flow_statement(
    db: DbSession,
    client: CurrentClient,
    start: date,
    end: date,
    granularity: Granularity = Granularity.MONTH,
    bank_account_ids: AccountFilter = None,
    today: Today = None,
    forecast: bool = True,
    scenario: ScenarioParam = Scenario.REALISTIC,
) -> CashFlowStatementOut:
    """DFC pelo método direto: operacional, financeiro e transferências por semana/mês."""
    _validate_range(start, end)
    report = AnalyticsService(db).cash_flow_statement(
        client.organization_id,
        client.id,
        start,
        end,
        granularity=granularity,
        bank_account_ids=bank_account_ids,
        today=today,
        forecast=ForecastOptions(enabled=forecast, scenario=scenario),
    )
    return CashFlowStatementOut.model_validate(report)
