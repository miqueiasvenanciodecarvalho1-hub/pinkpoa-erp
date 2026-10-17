"""Multi-tenancy: a Organization (consultoria) é o tenant; Clients (PF/PJ) pertencem a ela."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.enums import PersonType

if TYPE_CHECKING:
    from finance_engine.models.banking import BankAccount


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    clients: Mapped[list[Client]] = relationship(back_populates="organization")


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("organization_id", "document"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    person_type: Mapped[PersonType] = mapped_column(Enum(PersonType, native_enum=False, length=2))
    name: Mapped[str] = mapped_column(String(200))
    # Apenas dígitos: 11 (CPF) ou 14 (CNPJ). Validado na camada de schema.
    document: Mapped[str] = mapped_column(String(14))
    trade_name: Mapped[str | None] = mapped_column(String(200))
    # Cópia padronizada do cadastro da Receita (endereço, CNAE, QSA...) da última consulta.
    registry_data: Mapped[dict[str, Any] | None]
    is_active: Mapped[bool] = mapped_column(default=True)

    organization: Mapped[Organization] = relationship(back_populates="clients")
    bank_accounts: Mapped[list[BankAccount]] = relationship(back_populates="client")
