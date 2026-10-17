"""Registros cadastrais padronizados, independentes do provedor consultado."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Address:
    cep: str
    street: str = ""
    number: str = ""
    complement: str = ""
    district: str = ""
    city: str = ""
    state: str = ""
    ibge_code: str | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class Activity:
    code: str  # CNAE com 7 dígitos
    description: str


@dataclass(frozen=True, slots=True)
class RegisteredPartner:
    """Sócio do QSA. A Receita publica o CPF mascarado (***123456**); CNPJ vem completo."""

    name: str
    document: str
    kind: str  # "PF" | "PJ" | "EXTERIOR"
    role: str
    since: date | None = None


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    cnpj: str
    legal_name: str
    trade_name: str
    status: str  # ATIVA, BAIXADA, INAPTA, SUSPENSA, NULA
    status_date: date | None
    opened_at: date | None
    legal_nature: str
    size: str
    share_capital: Decimal
    simples: bool | None
    mei: bool | None
    main_activity: Activity | None
    secondary_activities: list[Activity]
    address: Address | None
    phone: str
    email: str
    head_office: bool
    partners: list[RegisteredPartner]
    source: str
    fetched_at: datetime
    alerts: list[str] = field(default_factory=list)
    suggested_sector: str | None = None
    suggested_specialties: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Bank:
    code: str  # COMPE, 3 dígitos
    ispb: str
    name: str
    full_name: str


@dataclass(frozen=True, slots=True)
class Holiday:
    date: date
    name: str
    kind: str
