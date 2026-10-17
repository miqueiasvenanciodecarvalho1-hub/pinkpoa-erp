"""Plano de categorias, centros de custo e regras de categorização (por tenant)."""

from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.enums import CategoryGroup, MatchType, TransactionType


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    group: Mapped[CategoryGroup] = mapped_column(Enum(CategoryGroup, native_enum=False, length=20))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )


class CostCenter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cost_centers"
    __table_args__ = (UniqueConstraint("organization_id", "client_id", "code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    # Centros de custo são do cliente (ex.: "Loja Centro", "Administrativo").
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))


class CategorizationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Regra palavra-chave -> categoria.

    ``client_id`` nulo = regra global da organização; preenchido = regra específica do cliente
    (tem precedência sobre a global quando a prioridade empata).
    """

    __tablename__ = "categorization_rules"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE")
    )
    pattern: Mapped[str] = mapped_column(String(200))
    match_type: Mapped[MatchType] = mapped_column(
        Enum(MatchType, native_enum=False, length=12), default=MatchType.CONTAINS
    )
    applies_to: Mapped[TransactionType | None] = mapped_column(
        Enum(TransactionType, native_enum=False, length=8)
    )
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="SET NULL")
    )
    priority: Mapped[int] = mapped_column(default=100)
    is_active: Mapped[bool] = mapped_column(default=True)

    category: Mapped[Category] = relationship()
