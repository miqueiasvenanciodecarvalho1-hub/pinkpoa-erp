"""Previsão de caixa por recorrência + cenários.

Inspirado no que as ferramentas de gestão de caixa líderes (Agicap, Float, Granatum, Conta Azul)
fazem para gerar o "saldo projetado" sem lançamento manual:

1. **Detecção de recorrência** — agrupa lançamentos históricos por "assinatura" do histórico
   (texto normalizado sem números/datas, ex.: ``"ALUGUEL"``, ``"PIX ENVIADO JOAO"``) e sinal;
   reconhece cadência semanal, quinzenal, mensal, bimestral, trimestral ou anual quando os
   intervalos são regulares e o valor é estável (coeficiente de variação baixo).
2. **Projeção** — repete cada padrão até o horizonte pedido, pulando datas em que já existe um
   lançamento real/agendado equivalente (evita contar em dobro uma conta a pagar já importada).
3. **Cenários** — fatores sobre entradas e saídas projetadas (realista, pessimista, otimista),
   para análise de sensibilidade do caixa.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from statistics import mean, median, pstdev

from finance_engine.analysis.cashflow import CashMovement
from finance_engine.ingestion.normalization import normalize_text

CENT = Decimal("0.01")
_NOISE_RE = re.compile(r"\d+|[^A-Z ]")
_STOPWORDS = frozenset({"DE", "DA", "DO", "DOS", "DAS", "E", "REF", "PARC", "PARCELA", "NR", "N"})


class Cadence(StrEnum):
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    BIMONTHLY = "BIMONTHLY"
    QUARTERLY = "QUARTERLY"
    YEARLY = "YEARLY"


# (cadência, intervalo nominal em dias, tolerância em dias por intervalo)
_CADENCES: tuple[tuple[Cadence, int, int], ...] = (
    (Cadence.WEEKLY, 7, 1),
    (Cadence.BIWEEKLY, 14, 2),
    (Cadence.MONTHLY, 30, 4),
    (Cadence.BIMONTHLY, 61, 5),
    (Cadence.QUARTERLY, 91, 7),
    (Cadence.YEARLY, 365, 10),
)


class Scenario(StrEnum):
    REALISTIC = "REALISTIC"
    PESSIMISTIC = "PESSIMISTIC"
    OPTIMISTIC = "OPTIMISTIC"


@dataclass(frozen=True, slots=True)
class ScenarioFactors:
    inflow: Decimal
    outflow: Decimal


SCENARIOS: dict[Scenario, ScenarioFactors] = {
    Scenario.REALISTIC: ScenarioFactors(Decimal("1"), Decimal("1")),
    # Pessimista: recebe 15% menos (inadimplência/atrasos) e gasta 10% mais.
    Scenario.PESSIMISTIC: ScenarioFactors(Decimal("0.85"), Decimal("1.10")),
    Scenario.OPTIMISTIC: ScenarioFactors(Decimal("1.10"), Decimal("0.95")),
}


@dataclass(frozen=True, slots=True)
class RecurringPattern:
    signature: str
    description: str  # exemplo legível (último histórico visto)
    category: str | None
    group: str | None
    cadence: Cadence
    amount: Decimal  # valor típico (mediana), com sinal
    occurrences: int
    last_date: date
    confidence: Decimal  # 0..1 — regularidade de intervalos e de valores


def description_signature(description: str) -> str:
    """Remove números, datas e ruído: "ALUGUEL 05/2024 PARC 3" -> "ALUGUEL"."""
    tokens = _NOISE_RE.sub(" ", normalize_text(description)).split()
    return " ".join(t for t in tokens if t not in _STOPWORDS and len(t) > 1)[:80]


def _classify_cadence(intervals: Sequence[int]) -> tuple[Cadence, Decimal] | None:
    typical = median(intervals)
    for cadence, nominal, tolerance in _CADENCES:
        if abs(typical - nominal) <= tolerance:
            within = sum(1 for i in intervals if abs(i - nominal) <= tolerance * 2)
            regularity = Decimal(within) / Decimal(len(intervals))
            if regularity >= Decimal("0.75"):
                return cadence, regularity
    return None


def detect_recurring(
    movements: Iterable[CashMovement],
    *,
    min_occurrences: int = 3,
    max_amount_cv: float = 0.25,
) -> list[RecurringPattern]:
    """Identifica lançamentos recorrentes no histórico.

    ``max_amount_cv``: coeficiente de variação máximo dos valores (0.25 = ±25%) — energia e
    folha variam um pouco mês a mês e continuam sendo recorrentes.
    """
    groups: dict[tuple[str, bool], list[CashMovement]] = defaultdict(list)
    for m in movements:
        if m.is_forecast or m.amount == 0:
            continue
        signature = description_signature(m.description)
        if signature:
            groups[(signature, m.amount > 0)].append(m)

    patterns: list[RecurringPattern] = []
    for (signature, _), items in groups.items():
        # Um lançamento por data (vários no mesmo dia são somados: ex. folha em várias TEDs).
        by_day: dict[date, Decimal] = defaultdict(Decimal)
        for m in items:
            by_day[m.date] += m.amount
        days = sorted(by_day)
        if len(days) < min_occurrences:
            continue
        intervals = [(b - a).days for a, b in itertools.pairwise(days)]
        cadence_info = _classify_cadence(intervals)
        if cadence_info is None:
            continue
        cadence, regularity = cadence_info
        amounts = [float(abs(by_day[d])) for d in days]
        avg = mean(amounts)
        cv = pstdev(amounts) / avg if avg else 1.0
        if cv > max_amount_cv:
            continue
        typical = Decimal(str(median(amounts))).quantize(CENT, ROUND_HALF_UP)
        sign = 1 if items[0].amount > 0 else -1
        last = max(items, key=lambda m: m.date)
        amount_stability = Decimal(str(max(0.0, 1 - cv / max_amount_cv)))
        confidence = ((regularity + amount_stability) / 2).quantize(Decimal("0.01"))
        patterns.append(
            RecurringPattern(
                signature=signature,
                description=last.description,
                category=last.category,
                group=last.group,
                cadence=cadence,
                amount=typical * sign,
                occurrences=len(days),
                last_date=days[-1],
                confidence=confidence,
            )
        )
    return sorted(patterns, key=lambda p: abs(p.amount), reverse=True)


def _next_date(current: date, cadence: Cadence, anchor_day: int) -> date:
    if cadence is Cadence.WEEKLY:
        return current + timedelta(days=7)
    if cadence is Cadence.BIWEEKLY:
        return current + timedelta(days=14)
    months = {Cadence.MONTHLY: 1, Cadence.BIMONTHLY: 2, Cadence.QUARTERLY: 3, Cadence.YEARLY: 12}[
        cadence
    ]
    month_index = current.month - 1 + months
    year, month = current.year + month_index // 12, month_index % 12 + 1
    # Mantém o "dia de vencimento" original, ajustando para meses mais curtos (31 -> 30/28).
    for day in range(anchor_day, 27, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, min(anchor_day, 28))


def project_recurring(
    patterns: Sequence[RecurringPattern],
    *,
    after: date,
    until: date,
    existing: Sequence[CashMovement] = (),
    scenario: Scenario = Scenario.REALISTIC,
    min_confidence: Decimal = Decimal("0.5"),
    match_window_days: int = 3,
) -> list[CashMovement]:
    """Gera lançamentos previstos em ``(after, until]``.

    Um lançamento previsto é suprimido quando já existe um lançamento real/agendado com a mesma
    assinatura e sinal dentro de ±``match_window_days`` — o dado real sempre vence a previsão.
    """
    factors = SCENARIOS[scenario]
    index: dict[tuple[str, bool], list[date]] = defaultdict(list)
    for m in existing:
        index[(description_signature(m.description), m.amount > 0)].append(m.date)

    window = timedelta(days=match_window_days)
    projected: list[CashMovement] = []
    for p in patterns:
        if p.confidence < min_confidence:
            continue
        factor = factors.inflow if p.amount > 0 else factors.outflow
        amount = (p.amount * factor).quantize(CENT, ROUND_HALF_UP)
        known = index.get((p.signature, p.amount > 0), [])
        current = p.last_date
        while True:
            current = _next_date(current, p.cadence, p.last_date.day)
            if current > until:
                break
            if current <= after or any(abs(d - current) <= window for d in known):
                continue
            projected.append(
                CashMovement(
                    date=current,
                    amount=amount,
                    category=p.category,
                    description=f"[PREVISTO] {p.description}",
                    group=p.group,
                    is_forecast=True,
                )
            )
    return sorted(projected, key=lambda m: m.date)
