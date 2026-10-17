"""Aging de contas a pagar e a receber: faixas de vencimento, concentração e inadimplência."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")

# (rótulo, dias mínimos, dias máximos) relativos ao vencimento: negativo = vencido há N dias.
BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("Vencido há mais de 90 dias", -10_000, -91),
    ("Vencido 61 a 90 dias", -90, -61),
    ("Vencido 31 a 60 dias", -60, -31),
    ("Vencido 1 a 30 dias", -30, -1),
    ("Vence em até 30 dias", 0, 30),
    ("Vence em 31 a 60 dias", 31, 60),
    ("Vence em 61 a 90 dias", 61, 90),
    ("Vence após 90 dias", 91, 10_000),
)


@dataclass(frozen=True, slots=True)
class AgingItem:
    counterparty: str
    due_date: date
    open_amount: Decimal  # positivo


@dataclass(frozen=True, slots=True)
class AgingBucket:
    label: str
    overdue: bool
    total: Decimal
    count: int
    share: Decimal


@dataclass(frozen=True, slots=True)
class CounterpartyExposure:
    counterparty: str
    total: Decimal
    overdue: Decimal
    share: Decimal
    oldest_overdue_days: int


@dataclass(frozen=True, slots=True)
class AgingReport:
    reference_date: date
    total_open: Decimal
    total_overdue: Decimal
    overdue_ratio: Decimal  # inadimplência (receber) ou atraso (pagar), 0..1
    weighted_days_to_due: Decimal  # prazo médio ponderado a vencer (negativo = em atraso)
    buckets: list[AgingBucket]
    top_counterparties: list[CounterpartyExposure]
    concentration_top5: Decimal  # fração do saldo nos 5 maiores


def build_aging(items: Iterable[AgingItem], reference_date: date, *, top: int = 10) -> AgingReport:
    items = [i for i in items if i.open_amount > 0]
    total = sum((i.open_amount for i in items), ZERO)
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[str, int] = defaultdict(int)
    by_cp: dict[str, list[AgingItem]] = defaultdict(list)
    weighted = ZERO
    for item in items:
        delta = (item.due_date - reference_date).days
        label = next(b[0] for b in BUCKETS if b[1] <= delta <= b[2])
        totals[label] += item.open_amount
        counts[label] += 1
        by_cp[item.counterparty].append(item)
        weighted += item.open_amount * delta
    overdue = sum((i.open_amount for i in items if i.due_date < reference_date), ZERO)

    def share(v: Decimal) -> Decimal:
        return (v / total).quantize(Decimal("0.0001")) if total else ZERO

    exposures = []
    for cp, lst in by_cp.items():
        cp_total = sum((i.open_amount for i in lst), ZERO)
        cp_over = [i for i in lst if i.due_date < reference_date]
        exposures.append(
            CounterpartyExposure(
                counterparty=cp,
                total=cp_total.quantize(CENT),
                overdue=sum((i.open_amount for i in cp_over), ZERO).quantize(CENT),
                share=share(cp_total),
                oldest_overdue_days=max(
                    ((reference_date - i.due_date).days for i in cp_over), default=0
                ),
            )
        )
    exposures.sort(key=lambda e: e.total, reverse=True)
    return AgingReport(
        reference_date=reference_date,
        total_open=total.quantize(CENT),
        total_overdue=overdue.quantize(CENT),
        overdue_ratio=share(overdue),
        weighted_days_to_due=(weighted / total).quantize(Decimal("0.1")) if total else ZERO,
        buckets=[
            AgingBucket(
                label=b[0],
                overdue=b[2] < 0,
                total=totals[b[0]].quantize(CENT),
                count=counts[b[0]],
                share=share(totals[b[0]]),
            )
            for b in BUCKETS
        ],
        top_counterparties=exposures[:top],
        concentration_top5=share(sum((e.total for e in exposures[:5]), ZERO)),
    )


def average_delay_days(pairs: Sequence[tuple[date, date]]) -> Decimal:
    """Atraso médio histórico (pagamento - vencimento): prevê quando o dinheiro entra."""
    if not pairs:
        return ZERO
    return Decimal(sum((paid - due).days for due, paid in pairs)) / len(pairs)
