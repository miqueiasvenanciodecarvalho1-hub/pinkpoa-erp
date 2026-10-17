"""Contas bancárias e lotes de importação."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.enums import ImportStatus, SourceFormat

if TYPE_CHECKING:
    from finance_engine.models.tenancy import Client


class BankAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("client_id", "bank_code", "branch", "account_number"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    bank_code: Mapped[str] = mapped_column(String(3))  # código COMPE: 001, 237, 341, 033...
    branch: Mapped[str] = mapped_column(String(10))
    account_number: Mapped[str] = mapped_column(String(20))
    nickname: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    # Saldo no início do dia ``opening_balance_date`` (antes dos lançamentos daquele dia).
    opening_balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    opening_balance_date: Mapped[date | None]
    is_active: Mapped[bool] = mapped_column(default=True)

    client: Mapped[Client] = relationship(back_populates="bank_accounts")


class ImportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rastreabilidade de cada arquivo importado (auditoria + idempotência por hash)."""

    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint("bank_account_id", "file_sha256"),
        Index("ix_import_batches_org_client", "organization_id", "client_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="CASCADE")
    )
    source_format: Mapped[SourceFormat] = mapped_column(
        Enum(SourceFormat, native_enum=False, length=10)
    )
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, native_enum=False, length=12), default=ImportStatus.PENDING
    )
    total_rows: Mapped[int] = mapped_column(default=0)
    imported_count: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    categorized_count: Mapped[int] = mapped_column(default=0)
    warnings: Mapped[list[str]] = mapped_column(default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
