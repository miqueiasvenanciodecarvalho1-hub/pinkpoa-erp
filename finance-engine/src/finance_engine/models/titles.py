"""Títulos a pagar e a receber e o vínculo com lançamentos do extrato (conciliação)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.enums import MatchMethod, TitleKind, TitleStatus


class FinancialTitle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Duplicata, boleto, fatura ou conta. Valores sempre positivos; o sentido vem de ``kind``."""

    __tablename__ = "financial_titles"
    __table_args__ = (
        Index("ix_titles_tenant_client_due", "organization_id", "client_id", "kind", "due_date"),
        UniqueConstraint("client_id", "kind", "fingerprint"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("open_amount >= 0 AND open_amount <= amount", name="open_amount_range"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    kind: Mapped[TitleKind] = mapped_column(Enum(TitleKind, native_enum=False, length=10))
    counterparty: Mapped[str] = mapped_column(
        String(200)
    )  # cliente (receber) ou fornecedor (pagar)
    counterparty_document: Mapped[str | None] = mapped_column(String(14))
    document_number: Mapped[str | None] = mapped_column(String(60))
    issue_date: Mapped[dt.date | None]
    due_date: Mapped[dt.date]
    amount: Mapped[Decimal]
    open_amount: Mapped[Decimal]
    status: Mapped[TitleStatus] = mapped_column(
        Enum(TitleStatus, native_enum=False, length=10), default=TitleStatus.OPEN
    )
    paid_date: Mapped[dt.date | None]
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    fingerprint: Mapped[str] = mapped_column(String(64))


class Reconciliation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Quanto de um lançamento do extrato liquida quanto de um título (permite N:1 e parciais)."""

    __tablename__ = "reconciliations"
    __table_args__ = (UniqueConstraint("title_id", "transaction_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    title_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("financial_titles.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal]
    method: Mapped[MatchMethod] = mapped_column(Enum(MatchMethod, native_enum=False, length=8))
    score: Mapped[Decimal] = mapped_column(default=Decimal("1"))
