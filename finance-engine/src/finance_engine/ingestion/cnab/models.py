"""Estruturas de saída dos parsers CNAB (independentes de 240/400)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class CnabFileKind(StrEnum):
    REMESSA = "REMESSA"
    RETORNO = "RETORNO"


@dataclass(slots=True)
class CnabHeader:
    bank_code: str
    bank_name: str
    kind: CnabFileKind
    generated_at: date | None
    company_document: str | None = None
    branch: str | None = None
    account_number: str | None = None


@dataclass(slots=True)
class CnabTitle:
    """Um título de cobrança (boleto) e o que aconteceu com ele no retorno."""

    line: int
    occurrence_code: str
    our_number: str  # nosso número
    document_number: str  # seu número / nº do documento
    due_date: date | None
    face_value: Decimal
    paid_amount: Decimal = Decimal("0")
    net_credited: Decimal = Decimal("0")
    interest: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    occurrence_date: date | None = None
    credit_date: date | None = None
    payer_name: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CnabFile:
    header: CnabHeader
    titles: list[CnabTitle]
    warnings: list[str] = field(default_factory=list)
