"""Indicadores de capital de giro, liquidez e equilíbrio (modelo Fleuriet + clássicos).

Prazos em dias sobre o período do demonstrativo (``days``, 360 para ano comercial):
* PMR = clientes / receita bruta × dias
* PME = estoques / CMV × dias
* PMP = fornecedores / compras × dias (compras ≈ CMV + variação de estoque; sem ela, CMV)
* Ciclo operacional = PME + PMR; ciclo financeiro (caixa) = ciclo operacional − PMP
* NCG = (clientes + estoques + outros AC operacionais)
  − (fornecedores + salários + impostos + outros PC operacionais)
* CDG = PL + passivo não circulante − ativo não circulante; saldo de tesouraria = CDG − NCG
  (negativo e piorando = "efeito tesoura": a operação cresce financiada por dívida de curto prazo)
* Ponto de equilíbrio = custos fixos / margem de contribuição %
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0")


def _d(v: Decimal, q: str = "0.01") -> Decimal:
    return v.quantize(Decimal(q))


@dataclass(slots=True)
class IndicatorAssumptions:
    days: int = 360
    variable_selling_share: Decimal = Decimal(
        "0.6"
    )  # parte das despesas de vendas que é variável (comissão, frete)
    previous_inventory: Decimal | None = None  # para estimar compras = CMV + Δestoque


@dataclass(slots=True)
class Indicators:
    pmr: Decimal | None = None
    pme: Decimal | None = None
    pmp: Decimal | None = None
    operating_cycle: Decimal | None = None
    cash_cycle: Decimal | None = None
    ncg: Decimal | None = None
    working_capital: Decimal | None = None  # CDG (Fleuriet)
    treasury_balance: Decimal | None = None
    current_ratio: Decimal | None = None
    quick_ratio: Decimal | None = None
    cash_ratio: Decimal | None = None
    debt_to_equity: Decimal | None = None
    gross_margin: Decimal | None = None
    net_margin: Decimal | None = None
    contribution_margin: Decimal | None = None
    fixed_costs: Decimal | None = None
    break_even_revenue: Decimal | None = None
    safety_margin: Decimal | None = None  # quanto a receita está acima do equilíbrio
    alerts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def compute_indicators(v: dict[str, Decimal], a: IndicatorAssumptions | None = None) -> Indicators:
    a = a or IndicatorAssumptions()
    g = v.get
    out = Indicators()
    days = Decimal(a.days)
    receita_bruta, receita_liq, cmv = (
        g("RECEITA_BRUTA") or g("RECEITA_LIQUIDA"),
        g("RECEITA_LIQUIDA"),
        g("CMV"),
    )
    clientes, estoques, fornecedores = g("AC_CLIENTES"), g("AC_ESTOQUES"), g("PC_FORNECEDORES")
    if clientes is not None and receita_bruta:
        out.pmr = _d(clientes / receita_bruta * days, "0.1")
    else:
        out.missing.append("PMR: falta clientes ou receita")
    if estoques is not None and cmv:
        out.pme = _d(estoques / cmv * days, "0.1")
    if fornecedores is not None and cmv:
        compras = cmv + (
            estoques - a.previous_inventory
            if estoques is not None and a.previous_inventory is not None
            else ZERO
        )
        out.pmp = _d(fornecedores / compras * days, "0.1") if compras > 0 else None
    else:
        out.missing.append("PMP: falta fornecedores ou CMV")
    if out.pmr is not None:
        out.operating_cycle = out.pmr + (out.pme or ZERO)
        if out.pmp is not None:
            out.cash_cycle = out.operating_cycle - out.pmp
    ac_oper = sum((g(k) or ZERO for k in ("AC_CLIENTES", "AC_ESTOQUES", "AC_OUTROS")), ZERO)
    pc_oper = sum(
        (g(k) or ZERO for k in ("PC_FORNECEDORES", "PC_SALARIOS", "PC_IMPOSTOS", "PC_OUTROS")), ZERO
    )
    if ac_oper or pc_oper:
        out.ncg = _d(ac_oper - pc_oper)
    if g("PL_TOTAL") is not None and g("ANC_TOTAL") is not None:
        out.working_capital = _d(g("PL_TOTAL") + (g("PNC_TOTAL") or ZERO) - g("ANC_TOTAL"))  # type: ignore[operator]
        if out.ncg is not None:
            out.treasury_balance = _d(out.working_capital - out.ncg)
    ac, pc = g("AC_TOTAL"), g("PC_TOTAL")
    if ac and pc:
        out.current_ratio = _d(ac / pc)
        out.quick_ratio = _d((ac - (estoques or ZERO)) / pc)
        out.cash_ratio = _d((g("AC_CAIXA") or ZERO) / pc)
    if g("PL_TOTAL"):
        out.debt_to_equity = _d(((pc or ZERO) + (g("PNC_TOTAL") or ZERO)) / g("PL_TOTAL"))  # type: ignore[operator]
    if receita_liq:
        if g("LUCRO_BRUTO") is not None:
            out.gross_margin = _d(g("LUCRO_BRUTO") / receita_liq, "0.0001")  # type: ignore[operator]
        if g("LUCRO_LIQUIDO") is not None:
            out.net_margin = _d(g("LUCRO_LIQUIDO") / receita_liq, "0.0001")  # type: ignore[operator]
        vendas = g("DESPESAS_VENDAS") or ZERO
        variable = (cmv or ZERO) + vendas * a.variable_selling_share
        out.contribution_margin = _d((receita_liq - variable) / receita_liq, "0.0001")
        net_fin = (g("DESPESAS_FINANCEIRAS") or ZERO) - (g("RECEITAS_FINANCEIRAS") or ZERO)
        out.fixed_costs = _d(
            (g("DESPESAS_ADM") or ZERO)
            + vendas * (1 - a.variable_selling_share)
            + max(net_fin, ZERO)
        )
        if out.contribution_margin > 0:
            out.break_even_revenue = _d(out.fixed_costs / out.contribution_margin)
            out.safety_margin = _d((receita_liq - out.break_even_revenue) / receita_liq, "0.0001")
    _alerts(out)
    return out


def _alerts(i: Indicators) -> None:
    if i.cash_cycle is not None and i.cash_cycle > 60:
        i.alerts.append(
            f"Ciclo financeiro de {i.cash_cycle} dias: a empresa financia {i.cash_cycle} dias "
            "de operação com caixa próprio ou dívida. Negocie prazo com fornecedores "
            "ou encurte o recebimento."
        )
    if i.pmr is not None and i.pmp is not None and i.pmr > i.pmp:
        i.alerts.append(
            f"Recebe em {i.pmr} dias e paga em {i.pmp}: o descasamento consome capital de giro."
        )
    if i.treasury_balance is not None and i.treasury_balance < 0:
        i.alerts.append(
            f"Saldo de tesouraria negativo (R$ {i.treasury_balance}): a necessidade de capital "
            "de giro é financiada por dívida de curto prazo (risco de efeito tesoura)."
        )
    if i.current_ratio is not None and i.current_ratio < 1:
        i.alerts.append(
            f"Liquidez corrente {i.current_ratio}: o ativo circulante não cobre "
            "as dívidas de curto prazo."
        )
    if i.safety_margin is not None and i.safety_margin < Decimal("0.1"):
        i.alerts.append(
            "Receita menos de 10% acima do ponto de equilíbrio: pouca margem para queda de vendas."
            if i.safety_margin >= 0
            else "Receita abaixo do ponto de equilíbrio: a operação dá prejuízo."
        )
    if i.debt_to_equity is not None and i.debt_to_equity > 2:
        i.alerts.append(f"Endividamento de {i.debt_to_equity}× o patrimônio líquido.")
