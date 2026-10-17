"""Indicadores de caixa: fluxo diário, saldo acumulado, ranking de despesas e anomalias.

Funções puras sobre ``CashMovement`` e ``Decimal`` — sem pandas aqui de propósito: cálculos
monetários em float acumulam erro de arredondamento, e o volume por cliente (milhares a dezenas
de milhares de lançamentos/ano) é trivial em Python puro. A agregação pesada fica no SQL.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from statistics import median

ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class CashMovement:
    date: date
    amount: Decimal  # + entrada / - saída
    category: str | None = None
    description: str = ""
    group: str | None = None  # CategoryGroup (para a DFC por atividade)
    is_forecast: bool = False  # gerado pelo motor de previsão, não importado


@dataclass(frozen=True, slots=True)
class DailyCashFlow:
    date: date
    inflow: Decimal
    outflow: Decimal  # positivo (valor absoluto das saídas)
    net: Decimal
    opening_balance: Decimal
    closing_balance: Decimal
    is_projected: bool


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category: str
    total: Decimal  # positivo
    share: Decimal  # fração do total de despesas (0..1)
    count: int


class AnomalyKind(StrEnum):
    NEGATIVE_BALANCE = "NEGATIVE_BALANCE"
    PROJECTED_NEGATIVE_BALANCE = "PROJECTED_NEGATIVE_BALANCE"
    LOW_BALANCE = "LOW_BALANCE"
    OUTFLOW_SPIKE = "OUTFLOW_SPIKE"
    EXPENSE_CONCENTRATION = "EXPENSE_CONCENTRATION"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Anomaly:
    kind: AnomalyKind
    severity: Severity
    start_date: date | None
    end_date: date | None
    value: Decimal
    message: str


@dataclass(frozen=True, slots=True)
class CashFlowSummary:
    opening_balance: Decimal
    closing_balance: Decimal
    total_inflow: Decimal
    total_outflow: Decimal
    net: Decimal
    min_balance: Decimal
    min_balance_date: date | None
    avg_daily_outflow: Decimal
    # Dias que o saldo final cobre a saída média diária (None se não há saídas).
    runway_days: Decimal | None


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def build_daily_cash_flow(
    movements: Iterable[CashMovement],
    *,
    opening_balance: Decimal,
    start: date,
    end: date,
    today: date,
    fill_gaps: bool = True,
) -> list[DailyCashFlow]:
    """Fluxo de caixa diário com saldo acumulado. Dias após ``today`` são projeção."""
    if end < start:
        raise ValueError("end deve ser >= start")
    inflow: dict[date, Decimal] = defaultdict(lambda: ZERO)
    outflow: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for m in movements:
        if not start <= m.date <= end:
            continue
        if m.amount >= 0:
            inflow[m.date] += m.amount
        else:
            outflow[m.date] += -m.amount

    days: Iterable[date]
    if fill_gaps:
        days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    else:
        days = sorted(set(inflow) | set(outflow))

    result: list[DailyCashFlow] = []
    balance = opening_balance
    for day in days:
        day_in, day_out = inflow.get(day, ZERO), outflow.get(day, ZERO)
        net = day_in - day_out
        result.append(
            DailyCashFlow(
                date=day,
                inflow=_q(day_in),
                outflow=_q(day_out),
                net=_q(net),
                opening_balance=_q(balance),
                closing_balance=_q(balance + net),
                is_projected=day > today,
            )
        )
        balance += net
    return result


def summarize(daily: Sequence[DailyCashFlow], opening_balance: Decimal) -> CashFlowSummary:
    total_in = sum((d.inflow for d in daily), ZERO)
    total_out = sum((d.outflow for d in daily), ZERO)
    closing = daily[-1].closing_balance if daily else opening_balance
    lowest = min(daily, key=lambda d: d.closing_balance, default=None)
    avg_out = _q(total_out / len(daily)) if daily else ZERO
    runway: Decimal | None = None
    if avg_out > 0:
        runway = _q(closing / avg_out) if closing > 0 else ZERO
    return CashFlowSummary(
        opening_balance=_q(opening_balance),
        closing_balance=_q(closing),
        total_inflow=_q(total_in),
        total_outflow=_q(total_out),
        net=_q(total_in - total_out),
        min_balance=lowest.closing_balance if lowest else _q(opening_balance),
        min_balance_date=lowest.date if lowest else None,
        avg_daily_outflow=avg_out,
        runway_days=runway,
    )


def top_expenses_by_category(
    movements: Iterable[CashMovement],
    *,
    limit: int = 10,
    uncategorized_label: str = "Sem categoria",
) -> list[CategoryTotal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[str, int] = defaultdict(int)
    for m in movements:
        if m.amount < 0:
            key = m.category or uncategorized_label
            totals[key] += -m.amount
            counts[key] += 1
    grand_total = sum(totals.values(), ZERO)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [
        CategoryTotal(
            category=name,
            total=_q(total),
            share=(total / grand_total).quantize(Decimal("0.0001")) if grand_total else ZERO,
            count=counts[name],
        )
        for name, total in ranked
    ]


def detect_anomalies(
    daily: Sequence[DailyCashFlow],
    expenses_by_category: Sequence[CategoryTotal] = (),
    *,
    low_balance_threshold: Decimal = ZERO,
    spike_mad_factor: Decimal = Decimal("5"),
    min_days_for_spike: int = 10,
    concentration_ratio: Decimal = Decimal("0.4"),
) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    anomalies.extend(_negative_balance_streaks(daily))
    if low_balance_threshold > 0:
        anomalies.extend(_low_balance_streaks(daily, low_balance_threshold))
    anomalies.extend(_outflow_spikes(daily, spike_mad_factor, min_days_for_spike))
    for cat in expenses_by_category:
        if cat.share >= concentration_ratio and len(expenses_by_category) > 1:
            anomalies.append(
                Anomaly(
                    kind=AnomalyKind.EXPENSE_CONCENTRATION,
                    severity=Severity.INFO,
                    start_date=None,
                    end_date=None,
                    value=cat.total,
                    message=(
                        f"'{cat.category}' concentra {cat.share * 100:.1f}% das despesas do período"
                    ),
                )
            )
    severity_rank = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(anomalies, key=lambda a: (severity_rank[a.severity], a.start_date or date.min))


def _streaks(
    daily: Sequence[DailyCashFlow], predicate: Callable[[DailyCashFlow], bool]
) -> list[list[DailyCashFlow]]:
    streaks: list[list[DailyCashFlow]] = []
    current: list[DailyCashFlow] = []
    for day in daily:
        if predicate(day):
            # Trechos não misturam dias realizados e projetados.
            if current and current[-1].is_projected != day.is_projected:
                streaks.append(current)
                current = []
            current.append(day)
        elif current:
            streaks.append(current)
            current = []
    if current:
        streaks.append(current)
    return streaks


def _negative_balance_streaks(daily: Sequence[DailyCashFlow]) -> list[Anomaly]:
    result: list[Anomaly] = []
    for streak in _streaks(daily, lambda d: d.closing_balance < 0):
        worst = min(streak, key=lambda d: d.closing_balance)
        projected = streak[0].is_projected
        label = "projetado" if projected else "realizado"
        result.append(
            Anomaly(
                kind=(
                    AnomalyKind.PROJECTED_NEGATIVE_BALANCE
                    if projected
                    else AnomalyKind.NEGATIVE_BALANCE
                ),
                severity=Severity.CRITICAL,
                start_date=streak[0].date,
                end_date=streak[-1].date,
                value=worst.closing_balance,
                message=(
                    f"Saldo {label} negativo por {len(streak)} dia(s) "
                    f"({streak[0].date:%d/%m/%Y} a {streak[-1].date:%d/%m/%Y}); "
                    f"pior saldo R$ {worst.closing_balance} em {worst.date:%d/%m/%Y}"
                ),
            )
        )
    return result


def _low_balance_streaks(daily: Sequence[DailyCashFlow], threshold: Decimal) -> list[Anomaly]:
    return [
        Anomaly(
            kind=AnomalyKind.LOW_BALANCE,
            severity=Severity.WARNING,
            start_date=s[0].date,
            end_date=s[-1].date,
            value=min(d.closing_balance for d in s),
            message=(
                f"Saldo abaixo do mínimo de R$ {threshold} por {len(s)} dia(s) "
                f"a partir de {s[0].date:%d/%m/%Y}"
            ),
        )
        for s in _streaks(daily, lambda d: 0 <= d.closing_balance < threshold)
    ]


def _outflow_spikes(
    daily: Sequence[DailyCashFlow], factor: Decimal, min_days: int
) -> list[Anomaly]:
    """Picos de saída via mediana + k·MAD (robusto a outliers, ao contrário de média/desvio)."""
    outflows = [d.outflow for d in daily if d.outflow > 0]
    if len(outflows) < min_days:
        return []
    med = Decimal(median(outflows))
    mad = Decimal(median([abs(v - med) for v in outflows]))
    # Saídas diárias quase constantes zeram o MAD; um piso de 10% da mediana evita que
    # qualquer centavo acima vire "pico" e, ao mesmo tempo, não cega o detector.
    scale = max(mad, med * Decimal("0.1"))
    threshold = med + factor * scale
    return [
        Anomaly(
            kind=AnomalyKind.OUTFLOW_SPIKE,
            severity=Severity.WARNING,
            start_date=d.date,
            end_date=d.date,
            value=d.outflow,
            message=(
                f"Saída atípica de R$ {d.outflow} em {d.date:%d/%m/%Y} "
                f"(mediana diária R$ {_q(med)})"
            ),
        )
        for d in daily
        if d.outflow > threshold
    ]
