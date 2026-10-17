"""Infraestrutura para layouts posicionais (largura fixa) FEBRABAN/CNAB.

Posições são declaradas exatamente como nos manuais dos bancos (1-based, inclusivas), o que
permite conferir cada campo diretamente contra o PDF do layout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.normalization import parse_compact_date


class FieldKind(StrEnum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"  # dígitos, mantidos como texto (preserva zeros à esquerda)
    MONEY = "MONEY"  # dígitos com N casas decimais implícitas
    DATE8 = "DATE8"  # DDMMAAAA
    DATE6 = "DATE6"  # DDMMAA


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    start: int  # 1-based, como nos manuais
    end: int  # inclusivo
    kind: FieldKind = FieldKind.TEXT
    decimals: int = 2

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ValueError(f"posições inválidas para {self.name}: {self.start}-{self.end}")

    def extract(self, line: str) -> str | Decimal | date | None:
        raw = line[self.start - 1 : self.end]
        if self.kind is FieldKind.TEXT:
            return raw.strip()
        if self.kind is FieldKind.NUMBER:
            return raw.strip() or None
        if self.kind is FieldKind.MONEY:
            digits = raw.strip()
            if not digits:
                return Decimal("0")
            if not digits.isdigit():
                raise RowParseError(f"campo {self.name} não numérico: {raw!r}")
            return Decimal(digits).scaleb(-self.decimals).quantize(Decimal("0.01"))
        return parse_compact_date(raw, year_digits=4 if self.kind is FieldKind.DATE8 else 2)


@dataclass(frozen=True, slots=True)
class RecordLayout:
    name: str
    fields: tuple[FieldSpec, ...]
    overrides: Mapping[str, FieldSpec] = field(default_factory=dict)

    def with_overrides(self, *specs: FieldSpec) -> RecordLayout:
        merged = {**self.overrides, **{s.name: s for s in specs}}
        return RecordLayout(self.name, self.fields, merged)

    def parse(self, line: str) -> dict[str, str | Decimal | date | None]:
        specs = {f.name: f for f in self.fields} | dict(self.overrides)
        return {name: spec.extract(line) for name, spec in specs.items()}


def split_fixed_width_lines(content: bytes, width: int) -> list[tuple[int, str]]:
    """Divide o arquivo em registros, tolerando CRLF, linhas finais vazias e espaços cortados."""
    text = content.decode("latin-1")  # CNAB é ASCII; latin-1 nunca falha
    lines: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.rstrip("\r\n\x1a")
        if not stripped.strip():
            continue
        if len(stripped) > width:
            raise ParseError(
                f"registro com {len(stripped)} posições (esperado {width})", line=number
            )
        lines.append((number, stripped.ljust(width)))
    if not lines:
        raise ParseError("arquivo CNAB vazio")
    return lines


def as_text(value: object) -> str:
    return "" if value is None else str(value)


def as_money(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal("0")


def as_date(value: object) -> date | None:
    return value if isinstance(value, date) else None
