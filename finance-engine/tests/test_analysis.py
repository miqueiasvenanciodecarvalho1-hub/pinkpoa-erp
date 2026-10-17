import uuid
from datetime import date, timedelta
from decimal import Decimal

from finance_engine.analysis import (
    AnomalyKind,
    Cadence,
    CashMovement,
    CategorizationEngine,
    RuleSpec,
    Scenario,
    build_cash_flow_statement,
    build_daily_cash_flow,
    detect_anomalies,
    detect_recurring,
    project_recurring,
    summarize,
    top_expenses_by_category,
)
from finance_engine.models import MatchType, TransactionType

D = Decimal


def _rule(pattern: str, **kwargs: object) -> RuleSpec:
    return RuleSpec(id=uuid.uuid4(), pattern=pattern, category_id=uuid.uuid4(), **kwargs)  # type: ignore[arg-type]


def test_categorization_word_boundary_and_priority() -> None:
    rede = _rule("REDE", applies_to=TransactionType.INCOME)
    tarifa = _rule("TARIFA", priority=110)
    pix = _rule("PIX")
    engine = CategorizationEngine([rede, tarifa, pix])

    assert engine.match("PAGTO PAREDE NOVA", TransactionType.EXPENSE) is None
    assert engine.match("CREDITO REDE VISA", TransactionType.INCOME).rule_id == rede.id  # type: ignore[union-attr]
    assert engine.match("CREDITO REDE VISA", TransactionType.EXPENSE) is None  # applies_to
    assert engine.match("TARIFA PIX", TransactionType.EXPENSE).rule_id == tarifa.id  # type: ignore[union-attr]


def test_categorization_client_rule_wins_tie_and_regex() -> None:
    org_rule = _rule("FORNECEDOR")
    client_rule = _rule("FORNECEDOR", client_specific=True)
    regex = _rule(r"^PIX .* JOAO$", match_type=MatchType.REGEX, priority=200)
    engine = CategorizationEngine([org_rule, client_rule, regex])
    assert engine.match("FORNECEDOR X", TransactionType.EXPENSE).rule_id == client_rule.id  # type: ignore[union-attr]
    assert engine.match("PIX ENVIADO JOAO", TransactionType.EXPENSE).rule_id == regex.id  # type: ignore[union-attr]


def test_daily_cash_flow_and_anomalies() -> None:
    start, today = date(2024, 1, 1), date(2024, 1, 10)
    movements = [
        CashMovement(start + timedelta(days=i), D("-100"), "Operacional") for i in range(12)
    ]
    movements += [
        CashMovement(date(2024, 1, 5), D("-5000"), "Fornecedores"),
        CashMovement(date(2024, 1, 3), D("3000"), "Vendas"),
        CashMovement(date(2024, 1, 14), D("-2500"), "Impostos"),  # projetado
    ]
    daily = build_daily_cash_flow(
        movements, opening_balance=D("2500"), start=start, end=date(2024, 1, 15), today=today
    )
    assert len(daily) == 15
    assert daily[0].closing_balance == D("2400.00")
    assert daily[4].closing_balance == D("0.00")  # 2500 - 5x100 + 3000 - 5000
    assert daily[-1].is_projected and not daily[9].is_projected

    summary = summarize(daily, D("2500"))
    assert summary.total_inflow == D("3000.00")
    assert summary.closing_balance == D("2500") + D("3000") - D("1200") - D("5000") - D("2500")

    top = top_expenses_by_category(movements)
    assert top[0].category == "Fornecedores"
    assert sum(t.share for t in top) == D("1.0000")

    anomalies = detect_anomalies(daily, top, low_balance_threshold=D("500"))
    kinds = {a.kind for a in anomalies}
    assert AnomalyKind.NEGATIVE_BALANCE in kinds
    assert AnomalyKind.PROJECTED_NEGATIVE_BALANCE in kinds
    assert AnomalyKind.LOW_BALANCE in kinds
    assert AnomalyKind.OUTFLOW_SPIKE in kinds
    assert AnomalyKind.EXPENSE_CONCENTRATION in kinds
    assert anomalies[0].severity == "CRITICAL"


def test_detect_recurring_and_project_with_scenarios() -> None:
    history = [
        CashMovement(
            date(2024, m, 10),
            D("-3200.00"),
            "Aluguel",
            f"ALUGUEL {m:02d}/2024",
            "OPERATING_EXPENSE",
        )
        for m in range(1, 7)
    ]
    history += [
        CashMovement(date(2024, m, 5), D(f"-{800 + m * 10}"), "Energia", "CEMIG ENERGIA REF", None)
        for m in range(1, 7)
    ]
    # ruído: não recorrente
    history += [CashMovement(date(2024, 2, 17), D("-999"), None, "COMPRA AVULSA")]

    patterns = {p.signature: p for p in detect_recurring(history)}
    assert set(patterns) == {"ALUGUEL", "CEMIG ENERGIA"}
    rent = patterns["ALUGUEL"]
    assert rent.cadence is Cadence.MONTHLY
    assert rent.amount == D("-3200.00")
    assert rent.confidence >= D("0.9")

    # Julho já tem o aluguel lançado (agendado) -> não projeta em dobro.
    existing = [CashMovement(date(2024, 7, 11), D("-3200.00"), None, "ALUGUEL 07/2024")]
    projected = project_recurring(
        [rent], after=date(2024, 6, 30), until=date(2024, 9, 30), existing=existing
    )
    assert [m.date for m in projected] == [date(2024, 8, 10), date(2024, 9, 10)]
    assert all(m.is_forecast for m in projected)

    pessimistic = project_recurring(
        [rent], after=date(2024, 6, 30), until=date(2024, 7, 31), scenario=Scenario.PESSIMISTIC
    )
    assert pessimistic[0].amount == D("-3520.00")


def test_monthly_anchor_day_handles_short_months() -> None:
    history = [CashMovement(date(2024, m, 31), D("-100"), None, "SEGURO") for m in (1, 3, 5, 7)]
    history += [CashMovement(date(2024, m, 30), D("-100"), None, "SEGURO") for m in (4, 6)]
    patterns = detect_recurring(history)
    assert patterns and patterns[0].cadence is Cadence.MONTHLY
    projected = project_recurring(patterns, after=date(2024, 7, 31), until=date(2024, 9, 30))
    assert [m.date for m in projected] == [date(2024, 8, 31), date(2024, 9, 30)]


def test_cash_flow_statement_monthly() -> None:
    movements = [
        CashMovement(date(2024, 1, 5), D("10000"), "Vendas", group="REVENUE"),
        CashMovement(date(2024, 1, 6), D("-4000"), "CMV", group="COGS"),
        CashMovement(date(2024, 1, 7), D("-50"), "Tarifas", group="FINANCIAL"),
        CashMovement(date(2024, 1, 8), D("-2000"), "Aplicação", group="TRANSFER"),
        CashMovement(date(2024, 2, 2), D("-300"), None),
        CashMovement(date(2024, 2, 10), D("-1000"), "CMV", group="COGS", is_forecast=True),
    ]
    periods = build_cash_flow_statement(
        movements, opening_balance=D("1000"), start=date(2024, 1, 1), end=date(2024, 2, 29)
    )
    jan, feb = periods
    assert jan.operating_cash_flow == D("6000")
    assert jan.financial_cash_flow == D("-50")
    assert jan.transfers == D("-2000")
    assert jan.closing_balance == D("4950")
    assert feb.opening_balance == D("4950")
    assert feb.unclassified == D("-300")
    assert feb.closing_balance == D("3650")
    assert feb.forecast_share == D("0.7692")
