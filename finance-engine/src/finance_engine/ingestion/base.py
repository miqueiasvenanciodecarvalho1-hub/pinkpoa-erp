"""Contrato comum dos parsers: todo formato vira uma lista de ``RawTransaction``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TypeVar

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.models.enums import SourceFormat

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RawTransaction:
    """Lançamento já tipado, porém ainda não vinculado a tenant/conta."""

    date: date
    description: str
    amount: Decimal  # com sinal: + crédito / - débito
    document: str | None = None
    external_id: str | None = None  # identificador único do banco (FITID, nosso número)
    line: int | None = None


@dataclass(slots=True)
class StatementMetadata:
    bank_code: str | None = None
    branch: str | None = None
    account_number: str | None = None
    currency: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    # Saldo informado pelo banco ao final de ``balance_date`` (ex.: LEDGERBAL do OFX).
    balance: Decimal | None = None
    balance_date: date | None = None


@dataclass(slots=True)
class ParseResult:
    source_format: SourceFormat
    transactions: list[RawTransaction] = field(default_factory=list)
    metadata: StatementMetadata = field(default_factory=StatementMetadata)
    warnings: list[str] = field(default_factory=list)
    total_rows: int = 0
    skipped_rows: int = 0


class StatementParser(ABC):
    """Parser de um formato. Implementações devem ser puras (bytes -> ParseResult)."""

    source_format: SourceFormat

    def __init__(self, *, max_invalid_ratio: float = 0.2) -> None:
        self.max_invalid_ratio = max_invalid_ratio

    @abstractmethod
    def parse(self, content: bytes) -> ParseResult: ...

    def _collect_rows(
        self,
        result: ParseResult,
        rows: Iterable[tuple[int, T]],
        convert: Callable[[int, T], RawTransaction | None],
    ) -> None:
        """Converte linha a linha; erros pontuais viram aviso, erros em massa abortam.

        ``convert`` devolve ``None`` para linhas que devem ser ignoradas sem erro
        (ex.: linhas de saldo, valores zerados).
        """
        invalid = 0
        for line, row in rows:
            result.total_rows += 1
            try:
                tx = convert(line, row)
            except RowParseError as exc:
                invalid += 1
                result.skipped_rows += 1
                result.warnings.append(f"Linha {line}: {exc}")
                continue
            if tx is None:
                result.skipped_rows += 1
            else:
                result.transactions.append(tx)

        if result.total_rows and invalid / result.total_rows > self.max_invalid_ratio:
            raise ParseError(
                f"{invalid} de {result.total_rows} linhas inválidas — verifique o formato "
                f"ou o mapeamento de colunas. Primeiros erros: {'; '.join(result.warnings[:3])}"
            )
        if not result.transactions:
            raise ParseError("nenhum lançamento válido encontrado no arquivo")
        dates = [t.date for t in result.transactions]
        result.metadata.period_start = result.metadata.period_start or min(dates)
        result.metadata.period_end = result.metadata.period_end or max(dates)
