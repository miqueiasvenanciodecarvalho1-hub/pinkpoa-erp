"""Demonstrativos: extração → mapeamento para o plano padrão → rascunho → validação."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_engine.analysis.chart_of_accounts import (
    STANDARD_ACCOUNTS,
    MappedStatement,
    complete,
    map_lines,
)
from finance_engine.analysis.indicators import IndicatorAssumptions, Indicators, compute_indicators
from finance_engine.core.exceptions import NotFoundError
from finance_engine.ingestion.statements import StatementLLM, extract_statements
from finance_engine.models import FinancialStatement, StatementKind, StatementStatus


class StatementsService:
    def __init__(self, session: Session, llm: StatementLLM | None = None) -> None:
        self.session = session
        self.llm = llm

    def upload(
        self,
        organization_id: uuid.UUID,
        client_id: uuid.UUID,
        content: bytes,
        filename: str,
        *,
        use_ai: bool = False,
    ) -> list[FinancialStatement]:
        created = []
        for ext in extract_statements(content, filename, llm=self.llm, use_ai=use_ai):
            mapped = map_lines(ext.lines)
            if ext.preset_codes:  # a IA já classificou; o mapeador só completa o que faltar
                mapped = MappedStatement(
                    values={**mapped.values, **ext.preset_codes},
                    matched=mapped.matched,
                    unmatched=mapped.unmatched,
                )
                complete(mapped)
            codes = {d: c for d, c in mapped.matched}
            st = FinancialStatement(
                organization_id=organization_id,
                client_id=client_id,
                kind=ext.kind,
                status=StatementStatus.DRAFT,
                period_start=ext.period_start,
                period_end=ext.period_end,
                source=ext.source,
                filename=filename[:255],
                values={k: str(v) for k, v in mapped.values.items()},
                lines=[
                    f"{ln.description}|{ln.value}|{codes.get(ln.description, '')}"
                    for ln in ext.lines
                ][:2000],
                warnings=[
                    *ext.warnings,
                    *mapped.warnings,
                    *(
                        [f"{len(mapped.unmatched)} linha(s) sem código padrão: revise"]
                        if mapped.unmatched
                        else []
                    ),
                ],
            )
            self.session.add(st)
            created.append(st)
        self.session.flush()
        return created

    def get(self, client_id: uuid.UUID, statement_id: uuid.UUID) -> FinancialStatement:
        st = self.session.get(FinancialStatement, statement_id)
        if st is None or st.client_id != client_id:
            raise NotFoundError("demonstrativo não encontrado")
        return st

    def update_values(
        self, st: FinancialStatement, values: dict[str, Decimal], validate: bool
    ) -> FinancialStatement:
        unknown = set(values) - set(STANDARD_ACCOUNTS)
        if unknown:
            raise NotFoundError(f"códigos desconhecidos: {', '.join(sorted(unknown))}")
        st.values = {**st.values, **{k: str(v) for k, v in values.items()}}
        if validate:
            st.status = StatementStatus.VALIDATED
        self.session.flush()
        return st

    def latest(self, client_id: uuid.UUID, kind: StatementKind) -> FinancialStatement | None:
        q = select(FinancialStatement).where(
            FinancialStatement.client_id == client_id, FinancialStatement.kind == kind
        )
        rows = list(
            self.session.scalars(
                q.order_by(
                    FinancialStatement.period_end.desc().nulls_last(),
                    FinancialStatement.created_at.desc(),
                )
            )
        )
        return next(
            (r for r in rows if r.status is StatementStatus.VALIDATED), rows[0] if rows else None
        )

    def indicators(
        self, client_id: uuid.UUID, assumptions: IndicatorAssumptions | None = None
    ) -> tuple[Indicators, list[FinancialStatement]]:
        used = [
            s
            for s in (
                self.latest(client_id, StatementKind.BALANCE_SHEET),
                self.latest(client_id, StatementKind.INCOME_STATEMENT),
            )
            if s
        ]
        values: dict[str, Decimal] = {}
        for s in used:
            values.update({k: Decimal(v) for k, v in s.values.items()})
        result = compute_indicators(values, assumptions)
        if any(s.status is StatementStatus.DRAFT for s in used):
            result.alerts.insert(
                0, "Calculado com demonstrativo ainda não validado pelo consultor."
            )
        return result, used
