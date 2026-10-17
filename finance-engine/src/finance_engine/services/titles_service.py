"""Contas a pagar/receber: importação, aging, conciliação e projeção no caixa."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance_engine.analysis.aging import AgingItem, AgingReport, average_delay_days, build_aging
from finance_engine.analysis.cashflow import CashMovement
from finance_engine.analysis.reconciliation import (
    BankEntry,
    MatchConfig,
    MatchSuggestion,
    OpenTitle,
    suggest_matches,
)
from finance_engine.core.exceptions import NotFoundError
from finance_engine.ingestion.normalization import normalize_text
from finance_engine.ingestion.titles import parse_titles
from finance_engine.models import (
    FinancialTitle,
    MatchMethod,
    Reconciliation,
    TitleKind,
    TitleStatus,
    Transaction,
)

ZERO = Decimal("0")
OPEN_STATES = (TitleStatus.OPEN, TitleStatus.PARTIAL)


@dataclass(frozen=True, slots=True)
class TitleImportResult:
    created: int
    updated: int
    errors: list[str]


def _fingerprint(
    kind: TitleKind, counterparty: str, number: str | None, due: date, amount: Decimal
) -> str:
    base = f"{kind}|{normalize_text(counterparty)}|{normalize_text(number or '')}|{due}|{amount}"
    return hashlib.sha256(base.encode()).hexdigest()


class TitlesService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def import_file(
        self, organization_id: uuid.UUID, client_id: uuid.UUID, content: bytes, *, receivable: bool
    ) -> TitleImportResult:
        raws, errors = parse_titles(content, default_receivable=receivable)
        created = updated = 0
        for r in raws:
            kind = TitleKind.RECEIVABLE if r.receivable else TitleKind.PAYABLE
            fp = _fingerprint(kind, r.counterparty, r.document_number, r.due_date, r.amount)
            title = self.session.scalar(
                select(FinancialTitle).where(
                    FinancialTitle.client_id == client_id,
                    FinancialTitle.kind == kind,
                    FinancialTitle.fingerprint == fp,
                )
            )
            status = (
                TitleStatus.PAID
                if r.open_amount == 0
                else TitleStatus.PARTIAL
                if r.open_amount < r.amount
                else TitleStatus.OPEN
            )
            if title is None:
                self.session.add(
                    FinancialTitle(
                        organization_id=organization_id,
                        client_id=client_id,
                        kind=kind,
                        counterparty=r.counterparty[:200],
                        counterparty_document=r.counterparty_document,
                        document_number=r.document_number,
                        issue_date=r.issue_date,
                        due_date=r.due_date,
                        amount=r.amount,
                        open_amount=r.open_amount,
                        status=status,
                        paid_date=r.paid_date,
                        fingerprint=fp,
                    )
                )
                created += 1
            else:  # reimportação atualiza o saldo (ex.: planilha do ERP exportada de novo)
                title.open_amount, title.status, title.paid_date = (
                    r.open_amount,
                    status,
                    r.paid_date or title.paid_date,
                )
                updated += 1
        self.session.flush()
        return TitleImportResult(created, updated, errors)

    def open_titles(
        self, client_id: uuid.UUID, kind: TitleKind | None = None
    ) -> list[FinancialTitle]:
        q = select(FinancialTitle).where(
            FinancialTitle.client_id == client_id,
            FinancialTitle.status.in_(OPEN_STATES),
            FinancialTitle.open_amount > 0,
        )
        if kind:
            q = q.where(FinancialTitle.kind == kind)
        return list(self.session.scalars(q.order_by(FinancialTitle.due_date)))

    def aging(self, client_id: uuid.UUID, kind: TitleKind, reference: date) -> AgingReport:
        return build_aging(
            [
                AgingItem(t.counterparty, t.due_date, t.open_amount)
                for t in self.open_titles(client_id, kind)
            ],
            reference,
        )

    # ------------------------------------------------------------------ conciliação

    def _unreconciled_entries(self, client_id: uuid.UUID, since: date) -> list[BankEntry]:
        allocated = dict(
            self.session.execute(
                select(Reconciliation.transaction_id, func.sum(Reconciliation.amount)).group_by(
                    Reconciliation.transaction_id
                )
            ).all()
        )
        txs = self.session.scalars(
            select(Transaction).where(Transaction.client_id == client_id, Transaction.date >= since)
        )
        out = []
        for t in txs:
            remaining = abs(t.amount) - Decimal(str(allocated.get(t.id, 0) or 0))
            if remaining > Decimal("0.01"):
                out.append(
                    BankEntry(
                        t.id, t.date, remaining if t.amount > 0 else -remaining, t.description
                    )
                )
        return out

    def suggest(
        self, client_id: uuid.UUID, today: date, cfg: MatchConfig | None = None
    ) -> list[MatchSuggestion]:
        titles = [
            OpenTitle(
                t.id,
                t.kind is TitleKind.RECEIVABLE,
                t.counterparty,
                t.counterparty_document,
                t.document_number,
                t.due_date,
                t.open_amount,
            )
            for t in self.open_titles(client_id)
        ]
        return suggest_matches(
            self._unreconciled_entries(client_id, today - timedelta(days=150)), titles, cfg
        )

    def apply(
        self,
        organization_id: uuid.UUID,
        client_id: uuid.UUID,
        matches: list[MatchSuggestion],
        method: MatchMethod,
    ) -> int:
        applied = 0
        for m in matches:
            tx = self.session.get(Transaction, m.transaction_id)
            if tx is None or tx.client_id != client_id:
                raise NotFoundError("lançamento não encontrado para este cliente")
            for title_id, amount in zip(m.title_ids, m.allocated, strict=True):
                title = self.session.get(FinancialTitle, title_id)
                if title is None or title.client_id != client_id:
                    raise NotFoundError("título não encontrado para este cliente")
                value = min(amount, title.open_amount)
                if value <= 0:
                    continue
                self.session.add(
                    Reconciliation(
                        organization_id=organization_id,
                        title_id=title.id,
                        transaction_id=tx.id,
                        amount=value,
                        method=method,
                        score=m.score,
                    )
                )
                title.open_amount -= value
                title.status = (
                    TitleStatus.PAID
                    if title.open_amount <= Decimal("0.01")
                    else TitleStatus.PARTIAL
                )
                if title.status is TitleStatus.PAID:
                    title.open_amount, title.paid_date = ZERO, tx.date
                applied += 1
        self.session.flush()
        return applied

    # ------------------------------------------------------------------ projeção no caixa

    def delay_profile(self, client_id: uuid.UUID) -> tuple[dict[str, Decimal], Decimal]:
        paid = self.session.execute(
            select(
                FinancialTitle.counterparty, FinancialTitle.due_date, FinancialTitle.paid_date
            ).where(
                FinancialTitle.client_id == client_id,
                FinancialTitle.kind == TitleKind.RECEIVABLE,
                FinancialTitle.paid_date.is_not(None),
            )
        ).all()
        by_cp: dict[str, list[tuple[date, date]]] = defaultdict(list)
        for cp, due, paid_date in paid:
            if paid_date is not None:
                by_cp[normalize_text(cp)].append((due, paid_date))
        overall = average_delay_days([p for v in by_cp.values() for p in v])
        return {k: average_delay_days(v) for k, v in by_cp.items()}, overall

    def scheduled_movements(
        self, client_id: uuid.UUID, today: date, *, doubtful_after_days: int = 90
    ) -> tuple[list[CashMovement], list[str]]:
        """Títulos em aberto como entradas/saídas futuras.

        Recebíveis entram no vencimento + atraso médio histórico da contraparte; vencidos há mais de
        ``doubtful_after_days`` dias são tratados como duvidosos e ficam fora da projeção.
        Contas a pagar vencidas entram no dia seguinte (precisam ser pagas).
        """
        per_cp, overall = self.delay_profile(client_id)
        notes: list[str] = []
        out: list[CashMovement] = []
        doubtful = ZERO
        for t in self.open_titles(client_id):
            if t.kind is TitleKind.RECEIVABLE:
                if (today - t.due_date).days > doubtful_after_days:
                    doubtful += t.open_amount
                    continue
                delay = int(per_cp.get(normalize_text(t.counterparty), overall))
                when = max(t.due_date + timedelta(days=max(delay, 0)), today + timedelta(days=1))
                out.append(
                    CashMovement(
                        when,
                        t.open_amount,
                        None,
                        f"[A RECEBER] {t.counterparty} {t.document_number or ''}".strip(),
                        "REVENUE",
                    )
                )
            else:
                when = max(t.due_date, today + timedelta(days=1))
                out.append(
                    CashMovement(
                        when,
                        -t.open_amount,
                        None,
                        f"[A PAGAR] {t.counterparty} {t.document_number or ''}".strip(),
                        None,
                    )
                )
        if doubtful:
            notes.append(
                f"R$ {doubtful} a receber vencidos há mais de {doubtful_after_days} dias "
                "ficaram fora da projeção (duvidosos)."
            )
        return out, notes

    def operational_terms(
        self, client_id: uuid.UUID, today: date, window_days: int = 180
    ) -> dict[str, Decimal | None]:
        """PMR/PMP operacionais: prazo médio real (emissão → pagamento) dos títulos quitados."""
        since = today - timedelta(days=window_days)
        res: dict[str, Decimal | None] = {}
        for kind, key in ((TitleKind.RECEIVABLE, "pmr"), (TitleKind.PAYABLE, "pmp")):
            rows = self.session.execute(
                select(
                    FinancialTitle.issue_date, FinancialTitle.paid_date, FinancialTitle.amount
                ).where(
                    FinancialTitle.client_id == client_id,
                    FinancialTitle.kind == kind,
                    FinancialTitle.paid_date >= since,
                    FinancialTitle.issue_date.is_not(None),
                )
            ).all()
            spans = [(amt, (paid - issued).days) for issued, paid, amt in rows if issued and paid]
            total = sum((amt for amt, _ in spans), ZERO)
            res[key] = (
                (sum((amt * d for amt, d in spans), ZERO) / total).quantize(Decimal("0.1"))
                if total
                else None
            )
        return res
