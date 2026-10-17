"""Simulação de antecipação de recebíveis (desconto de duplicatas, boletos, cartão).

Desconto comercial simples por título: ``deságio = valor × taxa_mensal × dias / 30``, com dias
contados da data da operação até o vencimento mais os dias de float (compensação). Sobre o valor
líquido incide IOF de operações de crédito de PJ: alíquota diária (0,0041% a.d., limitada a 365
dias) + adicional fixo de 0,38%. Tarifas por título e tarifa fixa (TAC) são descontadas no total.

O **custo efetivo (CET)** é a taxa mensal composta que iguala o valor recebido hoje ao fluxo dos
títulos nos vencimentos — é o número que permite comparar com outras linhas de crédito.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Receivable:
    amount: Decimal
    due_date: date
    reference: str = ""


@dataclass(slots=True)
class AnticipationTerms:
    monthly_rate: Decimal  # deságio, ex.: Decimal("0.025") = 2,5% a.m.
    operation_date: date
    float_days: int = 1
    fee_per_title: Decimal = ZERO
    fixed_fee: Decimal = ZERO  # TAC / tarifa de cadastro
    iof: bool = True
    iof_daily_rate: Decimal = Decimal(
        "0.000041"
    )  # PJ; optante do Simples (até R$ 30 mil): 0.00001367
    iof_additional_rate: Decimal = Decimal("0.0038")
    alternative_monthly_rate: Decimal | None = None  # ex.: capital de giro do banco, para comparar


@dataclass(frozen=True, slots=True)
class TitleResult:
    reference: str
    amount: Decimal
    due_date: date
    days: int
    discount: Decimal
    iof: Decimal
    fee: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class AnticipationResult:
    titles: list[TitleResult]
    face_value: Decimal
    discount: Decimal
    iof: Decimal
    fees: Decimal
    net_value: Decimal
    total_cost: Decimal
    cost_pct_of_face: Decimal
    weighted_days: Decimal  # prazo médio ponderado
    effective_monthly_rate: Decimal  # CET a.m.
    effective_annual_rate: Decimal  # CET a.a.
    alternative_cost: Decimal | None = None
    recommendation: str = ""
    warnings: list[str] = field(default_factory=list)


def _cet(net: Decimal, flows: Sequence[tuple[Decimal, int]]) -> Decimal:
    """Taxa mensal r tal que Σ valor / (1+r)^(dias/30) = líquido (bissecção)."""
    if net <= 0:
        return Decimal("0")
    lo, hi = 0.0, 1.0
    f_net = float(net)
    for _ in range(200):
        mid = (lo + hi) / 2
        pv = sum(float(a) / (1 + mid) ** (d / 30) for a, d in flows)
        if pv > f_net:
            lo = mid
        else:
            hi = mid
    return Decimal(str((lo + hi) / 2)).quantize(Decimal("0.000001"))


def simulate_anticipation(
    items: Sequence[Receivable], terms: AnticipationTerms
) -> AnticipationResult:
    if not items:
        raise ValueError("selecione ao menos um título")
    if terms.monthly_rate < 0:
        raise ValueError("taxa negativa")
    out: list[TitleResult] = []
    warnings: list[str] = []
    for it in items:
        days = (it.due_date - terms.operation_date).days + terms.float_days
        if days <= 0:
            warnings.append(f"Título {it.reference or it.amount} já venceu e foi ignorado.")
            continue
        discount = _q(it.amount * terms.monthly_rate * days / 30)
        base = it.amount - discount
        iof = ZERO
        if terms.iof:
            iof = _q(base * (terms.iof_daily_rate * min(days, 365) + terms.iof_additional_rate))
        fee = _q(terms.fee_per_title)
        out.append(
            TitleResult(
                it.reference,
                _q(it.amount),
                it.due_date,
                days,
                discount,
                iof,
                fee,
                _q(it.amount - discount - iof - fee),
            )
        )
    if not out:
        raise ValueError("nenhum título a vencer na data da operação")
    face = sum((t.amount for t in out), ZERO)
    discount = sum((t.discount for t in out), ZERO)
    iof = sum((t.iof for t in out), ZERO)
    fees = sum((t.fee for t in out), ZERO) + _q(terms.fixed_fee)
    net = face - discount - iof - fees
    if net <= 0:
        raise ValueError("os custos superam o valor dos títulos")
    weighted = (sum((t.amount * t.days for t in out), ZERO) / face).quantize(Decimal("0.1"))
    cet = _cet(net, [(t.amount, t.days) for t in out])
    annual = (Decimal(str((1 + float(cet)) ** 12 - 1))).quantize(Decimal("0.000001"))
    total_cost = face - net
    result_alt: Decimal | None = None
    rec = ""
    if terms.alternative_monthly_rate is not None:
        result_alt = _q(
            net
            * (
                Decimal(str((1 + float(terms.alternative_monthly_rate)) ** (float(weighted) / 30)))
                - 1
            )
        )
        if result_alt < total_cost:
            rec = (
                f"A linha alternativa ({terms.alternative_monthly_rate * 100:.2f}% a.m.) custaria "
                f"R$ {result_alt} pelo mesmo prazo, contra R$ {_q(total_cost)} da antecipação: "
                "vale mais a pena usar a linha alternativa."
            )
        else:
            rec = (
                f"A antecipação custa R$ {_q(total_cost)}, menos que os R$ {result_alt} da linha "
                "alternativa no mesmo prazo: a antecipação é a opção mais barata."
            )
    if cet > terms.monthly_rate * Decimal("1.15"):
        warnings.append(
            f"O custo efetivo ({cet * 100:.2f}% a.m.) é bem maior que o deságio anunciado "
            f"({terms.monthly_rate * 100:.2f}% a.m.) por causa de IOF, tarifas e float."
        )
    return AnticipationResult(
        titles=out,
        face_value=_q(face),
        discount=_q(discount),
        iof=_q(iof),
        fees=_q(fees),
        net_value=_q(net),
        total_cost=_q(total_cost),
        cost_pct_of_face=(total_cost / face).quantize(Decimal("0.0001")),
        weighted_days=weighted,
        effective_monthly_rate=cet,
        effective_annual_rate=annual,
        alternative_cost=result_alt,
        recommendation=rec,
        warnings=warnings,
    )
