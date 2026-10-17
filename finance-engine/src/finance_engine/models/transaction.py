"""Lançamento financeiro padronizado — destino final de todos os parsers."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.classification import Category, CostCenter
from finance_engine.models.enums import (
    CategorizationSource,
    ReconciliationStatus,
    SourceFormat,
    TransactionType,
)


class Transaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # Idempotência: o mesmo lançamento nunca entra duas vezes na mesma conta,
        # mesmo que extratos com períodos sobrepostos sejam importados.
        UniqueConstraint("bank_account_id", "fingerprint"),
        Index("ix_transactions_tenant_client_date", "organization_id", "client_id", "date"),
        Index("ix_transactions_account_date", "bank_account_id", "date"),
        # Valor com sinal (+ receita / - despesa) coerente com o tipo: somas diretas em SQL.
        CheckConstraint(
            "(type = 'INCOME' AND amount > 0) OR (type = 'EXPENSE' AND amount < 0)",
            name="amount_sign_matches_type",
        ),
    )

    # organization_id é desnormalizado de propósito: todo filtro de tenant é 1 coluna indexada.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="CASCADE")
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), index=True
    )

    date: Mapped[dt.date]
    description: Mapped[str] = mapped_column(String(500))  # histórico original
    normalized_description: Mapped[str] = mapped_column(String(500))  # sem acento, maiúsculo
    amount: Mapped[Decimal]
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, native_enum=False, length=8)
    )
    document: Mapped[str | None] = mapped_column(String(60))
    external_id: Mapped[str | None] = mapped_column(String(120))  # FITID / nosso número
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_format: Mapped[SourceFormat] = mapped_column(
        Enum(SourceFormat, native_enum=False, length=10)
    )

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="SET NULL")
    )
    categorization_source: Mapped[CategorizationSource] = mapped_column(
        Enum(CategorizationSource, native_enum=False, length=8),
        default=CategorizationSource.NONE,
    )
    categorization_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categorization_rules.id", ondelete="SET NULL")
    )

    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus, native_enum=False, length=10),
        default=ReconciliationStatus.PENDING,
    )
    reconciled_at: Mapped[dt.datetime | None]

    category: Mapped[Category | None] = relationship()
    cost_center: Mapped[CostCenter | None] = relationship()
