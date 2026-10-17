"""Demonstrativos contábeis padronizados (rascunho extraído → validado pelo consultor)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.enums import StatementKind, StatementStatus


class FinancialStatement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_statements"
    __table_args__ = (Index("ix_statements_client_period", "client_id", "kind", "period_end"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    kind: Mapped[StatementKind] = mapped_column(Enum(StatementKind, native_enum=False, length=20))
    status: Mapped[StatementStatus] = mapped_column(
        Enum(StatementStatus, native_enum=False, length=10), default=StatementStatus.DRAFT
    )
    period_start: Mapped[dt.date | None]
    period_end: Mapped[dt.date | None]
    source: Mapped[str] = mapped_column(String(60))
    filename: Mapped[str] = mapped_column(String(255))
    values: Mapped[dict[str, Any]]  # código padrão -> valor (string decimal)
    lines: Mapped[list[str]]  # linhas originais "descrição|valor|código" para auditoria
    warnings: Mapped[list[str]]
