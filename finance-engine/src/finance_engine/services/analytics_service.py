"""Relatórios de caixa de um cliente: busca no banco e delega o cálculo a ``analysis``."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance_engine.analysis.cashflow import (
    Anomaly,
    CashFlowSummary,
    CashMovement,
    CategoryTotal,
    DailyCashFlow,
    build_daily_cash_flow,
    detect_anomalies,
    summarize,
    top_expenses_by_category,
)
from finance_engine.analysis.forecast import (
    RecurringPattern,
    Scenario,
    detect_recurring,
    project_recurring,
)
from finance_engine.analysis.statements import (
    Granularity,
    StatementPeriod,
    build_cash_flow_statement,
)
from finance_engine.core.config import Settings, get_settings
from finance_engine.core.exceptions import NotFoundError
from finance_engine.models import BankAccount, Category, CategoryGroup, Client, Transaction

ZERO = Decimal("0")

# Grupos neutros para o resultado: entram no saldo, mas não no ranking de despesas.
NON_EXPENSE_GROUPS = frozenset({CategoryGroup.TRANSFER})


@dataclass(frozen=True, slots=True)
class ForecastOptions:
    enabled: bool = True
    scenario: Scenario = Scenario.REALISTIC
    history_days: int = 180  # janela usada para aprender os padrões recorrentes


@dataclass(frozen=True, slots=True)
class _Dataset:
    account_ids: list[uuid.UUID]
    opening_balance: Decimal
    movements: list[CashMovement]  # reais + previstos, ordenados por data
    patterns: list[RecurringPattern]


@dataclass(frozen=True, slots=True)
class CashFlowReport:
    client_id: uuid.UUID
    start: date
    end: date
    today: date
    scenario: Scenario | None
    bank_account_ids: list[uuid.UUID]
    summary: CashFlowSummary
    daily: list[DailyCashFlow]
    top_expenses: list[CategoryTotal]
    anomalies: list[Anomaly]
    recurring_patterns: list[RecurringPattern]


@dataclass(frozen=True, slots=True)
class CashFlowStatementReport:
    client_id: uuid.UUID
    granularity: Granularity
    scenario: Scenario | None
    periods: list[StatementPeriod]


class AnalyticsService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ relatórios

    def cash_flow_report(
        self,
        organization_id: uuid.UUID,
        client_id: uuid.UUID,
        start: date,
        end: date,
        *,
        bank_account_ids: list[uuid.UUID] | None = None,
        today: date | None = None,
        top_n: int = 10,
        low_balance_threshold: Decimal | None = None,
        forecast: ForecastOptions | None = None,
    ) -> CashFlowReport:
        today = today or date.today()
        forecast = forecast or ForecastOptions()
        data = self._dataset(
            organization_id, client_id, start, end, bank_account_ids, today, forecast
        )
        daily = build_daily_cash_flow(
            data.movements, opening_balance=data.opening_balance, start=start, end=end, today=today
        )
        realized_expenses = [
            m for m in data.movements if not m.is_forecast and m.group not in NON_EXPENSE_GROUPS
        ]
        top = top_expenses_by_category(realized_expenses, limit=top_n)
        anomalies = detect_anomalies(
            daily,
            top,
            low_balance_threshold=(
                low_balance_threshold
                if low_balance_threshold is not None
                else self.settings.low_balance_threshold
            ),
            spike_mad_factor=self.settings.outflow_spike_mad_factor,
            concentration_ratio=self.settings.category_concentration_ratio,
        )
        return CashFlowReport(
            client_id=client_id,
            start=start,
            end=end,
            today=today,
            scenario=forecast.scenario if forecast.enabled else None,
            bank_account_ids=data.account_ids,
            summary=summarize(daily, data.opening_balance),
            daily=daily,
            top_expenses=top,
            anomalies=anomalies,
            recurring_patterns=data.patterns,
        )

    def cash_flow_statement(
        self,
        organization_id: uuid.UUID,
        client_id: uuid.UUID,
        start: date,
        end: date,
        *,
        granularity: Granularity = Granularity.MONTH,
        bank_account_ids: list[uuid.UUID] | None = None,
        today: date | None = None,
        forecast: ForecastOptions | None = None,
    ) -> CashFlowStatementReport:
        forecast = forecast or ForecastOptions()
        data = self._dataset(
            organization_id,
            client_id,
            start,
            end,
            bank_account_ids,
            today or date.today(),
            forecast,
        )
        periods = build_cash_flow_statement(
            data.movements,
            opening_balance=data.opening_balance,
            start=start,
            end=end,
            granularity=granularity,
        )
        return CashFlowStatementReport(
            client_id=client_id,
            granularity=granularity,
            scenario=forecast.scenario if forecast.enabled else None,
            periods=periods,
        )

    # ------------------------------------------------------------------ dados

    def _dataset(
        self,
        organization_id: uuid.UUID,
        client_id: uuid.UUID,
        start: date,
        end: date,
        bank_account_ids: list[uuid.UUID] | None,
        today: date,
        forecast: ForecastOptions,
    ) -> _Dataset:
        client = self.session.get(Client, client_id)
        if client is None or client.organization_id != organization_id:
            raise NotFoundError("cliente não encontrado")

        accounts_query = select(BankAccount).where(
            BankAccount.organization_id == organization_id, BankAccount.client_id == client_id
        )
        if bank_account_ids:
            accounts_query = accounts_query.where(BankAccount.id.in_(bank_account_ids))
        accounts = list(self.session.scalars(accounts_query))
        if bank_account_ids and len(accounts) != len(set(bank_account_ids)):
            raise NotFoundError("uma ou mais contas não pertencem a este cliente")
        account_ids = [a.id for a in accounts]

        opening = sum((self._balance_at(a, start) for a in accounts), ZERO)
        movements = self._movements(organization_id, client_id, account_ids, start, end)

        patterns: list[RecurringPattern] = []
        if forecast.enabled and end > today:
            history_start = today - timedelta(days=forecast.history_days)
            history = self._movements(organization_id, client_id, account_ids, history_start, today)
            patterns = detect_recurring(history)
            already_known = [m for m in movements if m.date > today - timedelta(days=7)]
            projected = project_recurring(
                patterns,
                after=max(today, start - timedelta(days=1)),
                until=end,
                existing=already_known,
                scenario=forecast.scenario,
            )
            movements = sorted(movements + projected, key=lambda m: m.date)
        return _Dataset(account_ids, opening, movements, patterns)

    def _movements(
        self,
        organization_id: uuid.UUID,
        client_id: uuid.UUID,
        account_ids: list[uuid.UUID],
        start: date,
        end: date,
    ) -> list[CashMovement]:
        rows = self.session.execute(
            select(
                Transaction.date,
                Transaction.amount,
                Category.name,
                Category.group,
                Transaction.description,
            )
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.organization_id == organization_id,
                Transaction.client_id == client_id,
                Transaction.bank_account_id.in_(account_ids),
                Transaction.date.between(start, end),
            )
            .order_by(Transaction.date)
        ).all()
        return [
            CashMovement(
                date=r[0],
                amount=r[1],
                category=r[2],
                description=r[4],
                group=str(r[3]) if r[3] is not None else None,
            )
            for r in rows
        ]

    def _balance_at(self, account: BankAccount, day: date) -> Decimal:
        """Saldo no início de ``day``, ancorado no saldo inicial cadastrado da conta.

        saldo(d) = saldo_inicial + Σ[data_ref <= t < d] - Σ[d <= t < data_ref]
        (a segunda soma cobre consultas a períodos anteriores à data de referência).
        """

        def total(*conditions: object) -> Decimal:
            value = self.session.scalar(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.bank_account_id == account.id,
                    *conditions,  # type: ignore[arg-type]
                )
            )
            return Decimal(str(value or 0))

        anchor = account.opening_balance_date
        if anchor is None:
            return account.opening_balance + total(Transaction.date < day)
        if day >= anchor:
            return account.opening_balance + total(
                Transaction.date >= anchor, Transaction.date < day
            )
        return account.opening_balance - total(Transaction.date >= day, Transaction.date < anchor)
