"""Demonstração de Fluxo de Caixa (DFC) — método direto, por período e atividade.

Formato usado pelos sistemas de gestão financeira brasileiros (Conta Azul, Nibo, Omie): cada
período mostra entradas/saídas por grupo gerencial, o resultado operacional de caixa, o efeito
das atividades financeiras e das transferências, e o saldo final.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from finance_engine.analysis.cashflow import CashMovement
from finance_engine.models.enums import CategoryGroup

ZERO = Decimal("0")

OPERATING_GROUPS = frozenset(
    {
        CategoryGroup.REVENUE,
        CategoryGroup.COGS,
        CategoryGroup.OPERATING_EXPENSE,
        CategoryGroup.PAYROLL,
        CategoryGroup.TAXES,
        CategoryGroup.PERSONAL,
        CategoryGroup.OTHER,
    }
)
UNCLASSIFIED = "UNCLASSIFIED"


class Granularity(StrEnum):
    WEEK = "WEEK"
    MONTH = "MONTH"


@dataclass(slots=True)
class StatementPeriod:
    period_start: date
    period_end: date
    opening_balance: Decimal
    inflow_by_group: dict[str, Decimal] = field(default_factory=dict)
    outflow_by_group: dict[str, Decimal] = field(default_factory=dict)
    operating_cash_flow: Decimal = ZERO  # geração de caixa operacional
    financial_cash_flow: Decimal = ZERO  # tarifas, juros, rendimentos
    transfers: Decimal = ZERO  # aplicações/resgates/entre contas
    unclassified: Decimal = ZERO  # sem categoria — sinaliza trabalho de categorização pendente
    net: Decimal = ZERO
    closing_balance: Decimal = ZERO
    forecast_share: Decimal = ZERO  # fração do movimento do período que é previsão


def _period_bounds(day: date, granularity: Granularity) -> tuple[date, date]:
    if granularity is Granularity.WEEK:
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    start = day.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)


def build_cash_flow_statement(
    movements: Iterable[CashMovement],
    *,
    opening_balance: Decimal,
    start: date,
    end: date,
    granularity: Granularity = Granularity.MONTH,
) -> list[StatementPeriod]:
    buckets: dict[date, list[CashMovement]] = defaultdict(list)
    for m in movements:
        if start <= m.date <= end:
            buckets[_period_bounds(m.date, granularity)[0]].append(m)

    periods: list[StatementPeriod] = []
    balance = opening_balance
    cursor = _period_bounds(start, granularity)[0]
    while cursor <= end:
        p_start, p_end = _period_bounds(cursor, granularity)
        period = StatementPeriod(
            period_start=max(p_start, start), period_end=min(p_end, end), opening_balance=balance
        )
        inflow: dict[str, Decimal] = defaultdict(lambda: ZERO)
        outflow: dict[str, Decimal] = defaultdict(lambda: ZERO)
        gross = forecast = ZERO
        for m in buckets.get(p_start, []):
            group = m.group or UNCLASSIFIED
            if m.amount > 0:
                inflow[group] += m.amount
            else:
                outflow[group] += -m.amount
            gross += abs(m.amount)
            if m.is_forecast:
                forecast += abs(m.amount)
            if group in OPERATING_GROUPS:
                period.operating_cash_flow += m.amount
            elif group == CategoryGroup.FINANCIAL:
                period.financial_cash_flow += m.amount
            elif group == CategoryGroup.TRANSFER:
                period.transfers += m.amount
            else:
                period.unclassified += m.amount
            period.net += m.amount
        period.inflow_by_group = dict(inflow)
        period.outflow_by_group = dict(outflow)
        period.forecast_share = (forecast / gross).quantize(Decimal("0.0001")) if gross else ZERO
        balance += period.net
        period.closing_balance = balance
        periods.append(period)
        cursor = p_end + timedelta(days=1)
    return periods
