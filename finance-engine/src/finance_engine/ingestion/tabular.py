"""Parser de extratos/planilhas tabulares (CSV e XLSX) com mapeamento flexível de colunas.

Extratos exportados por bancos e ERPs brasileiros variam muito: separador ``;``, encoding
CP1252, linhas de cabeçalho institucional antes da tabela, crédito/débito em colunas separadas,
coluna "D/C", linhas de "SALDO DO DIA" no meio dos lançamentos. O parser:

1. lê tudo como texto/objeto, sem inferência de tipos do pandas (evita datas e decimais errados);
2. localiza a linha de cabeçalho automaticamente (ou usa a indicada);
3. resolve as colunas por um mapeamento explícito ou por sinônimos conhecidos;
4. converte linha a linha com as funções de ``normalization`` (Decimal, datas BR).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.base import ParseResult, RawTransaction, StatementParser
from finance_engine.ingestion.normalization import (
    DATE_FORMATS,
    decode_bytes,
    normalize_text,
    parse_amount,
    parse_date,
)
from finance_engine.models.enums import SourceFormat

_HEADER_SCAN_ROWS = 40
_EXCEL_EPOCH = date(1899, 12, 30)
_HEADER_NOISE_RE = re.compile(r"\(?R\$\)?|[^A-Z0-9/ ]")

SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": (
        "DATA",
        "DT",
        "DATE",
        "DATA LANCAMENTO",
        "DT LANCAMENTO",
        "DATA MOVIMENTO",
        "DATA MOV",
        "DATA OPERACAO",
        "DATA TRANSACAO",
        "DATA DA TRANSACAO",
        "VENCIMENTO",
        "DATA VENCIMENTO",
        "DATA PAGAMENTO",
    ),
    "description": (
        "DESCRICAO",
        "HISTORICO",
        "LANCAMENTO",
        "LANCAMENTOS",
        "DESCRIPTION",
        "MEMO",
        "DETALHE",
        "DETALHES",
        "HISTORICO/DESCRICAO",
        "ESTABELECIMENTO",
        "FAVORECIDO",
    ),
    "amount": ("VALOR", "AMOUNT", "VLR", "VALOR LANCAMENTO", "MONTANTE", "VALOR TOTAL"),
    "credit": ("CREDITO", "CREDITOS", "ENTRADA", "ENTRADAS", "RECEBIMENTOS"),
    "debit": ("DEBITO", "DEBITOS", "SAIDA", "SAIDAS", "PAGAMENTOS"),
    "document": (
        "DOCUMENTO",
        "DOC",
        "DOCTO",
        "N DOCUMENTO",
        "NO DOCUMENTO",
        "N DOC",
        "NUM DOCUMENTO",
        "NUMERO DOCUMENTO",
        "NUMERO DO DOCUMENTO",
    ),
    "dc_indicator": ("D/C", "C/D", "DC", "NATUREZA", "TIPO", "CREDITO/DEBITO"),
}


def normalize_header(value: object) -> str:
    text = normalize_text(str(value)).replace("º", "").replace("°", "")
    text = _HEADER_NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


_SYNONYM_INDEX: dict[str, str] = {
    normalize_header(s): role for role, names in SYNONYMS.items() for s in names
}


@dataclass(slots=True)
class ColumnMapping:
    """Nomes das colunas no arquivo de origem. Campos ``None`` são auto-detectados.

    ``description`` aceita várias colunas (serão concatenadas com " - ").
    Use ``amount`` (valor com sinal) **ou** ``credit``/``debit`` (colunas separadas).
    """

    date: str | None = None
    description: list[str] = field(default_factory=list)
    amount: str | None = None
    credit: str | None = None
    debit: str | None = None
    document: str | None = None
    dc_indicator: str | None = None


@dataclass(slots=True)
class TabularOptions:
    mapping: ColumnMapping = field(default_factory=ColumnMapping)
    sheet_name: str | int = 0
    header_row: int | None = None  # 0-based; None = detecta automaticamente
    date_format: str | None = None  # ex.: "%d/%m/%Y"; None = tenta os formatos comuns
    invert_sign: bool = False  # faturas de cartão costumam listar compras como positivas
    skip_description_patterns: tuple[str, ...] = (
        "SALDO ANTERIOR",
        "SALDO DO DIA",
        "SALDO FINAL",
        "SALDO INICIAL",
        "SALDO EM",
        "S A L D O",
    )


@dataclass(frozen=True, slots=True)
class _ResolvedColumns:
    date: int
    description: list[int]
    amount: int | None
    credit: int | None
    debit: int | None
    document: int | None
    dc_indicator: int | None


class TabularParser(StatementParser):
    """Base comum para CSV e XLSX; subclasses só implementam a leitura do grid bruto."""

    def __init__(
        self, options: TabularOptions | None = None, *, max_invalid_ratio: float = 0.2
    ) -> None:
        super().__init__(max_invalid_ratio=max_invalid_ratio)
        self.options = options or TabularOptions()

    def _read_grid(self, content: bytes) -> pd.DataFrame:
        raise NotImplementedError

    def parse(self, content: bytes) -> ParseResult:
        grid = self._read_grid(content)
        if grid.empty:
            raise ParseError("planilha vazia")
        header_idx = (
            self.options.header_row
            if self.options.header_row is not None
            else self._detect_header_row(grid)
        )
        headers = [str(v).strip() for v in grid.iloc[header_idx].tolist()]
        columns = self._resolve_columns(headers)

        result = ParseResult(source_format=self.source_format)
        body = grid.iloc[header_idx + 1 :]
        rows = (
            (int(idx) + 1, [None if _is_blank(v) else v for v in row])
            for idx, row in zip(body.index, body.itertuples(index=False, name=None), strict=True)
        )
        skip_patterns = tuple(normalize_text(p) for p in self.options.skip_description_patterns)

        def convert(line: int, row: list[Any]) -> RawTransaction | None:
            return self._convert_row(line, row, columns, skip_patterns)

        self._collect_rows(result, rows, convert)
        return result

    # ------------------------------------------------------------------ cabeçalho / colunas

    def _detect_header_row(self, grid: pd.DataFrame) -> int:
        mapping = self.options.mapping
        explicit = {
            normalize_header(c)
            for c in (
                mapping.date,
                mapping.amount,
                mapping.credit,
                mapping.debit,
                *mapping.description,
            )
            if c
        }
        for idx in range(min(_HEADER_SCAN_ROWS, len(grid))):
            cells = {normalize_header(v) for v in grid.iloc[idx].tolist() if not _is_blank(v)}
            if explicit and explicit <= cells:
                return idx
            roles = {_SYNONYM_INDEX[c] for c in cells if c in _SYNONYM_INDEX}
            has_value = "amount" in roles or {"credit", "debit"} & roles
            if not explicit and "date" in roles and has_value and len(roles) >= 2:
                return idx
        raise ParseError(
            "cabeçalho não encontrado: informe o mapeamento de colunas (data, descrição, valor)"
        )

    def _resolve_columns(self, headers: list[str]) -> _ResolvedColumns:
        normalized = [normalize_header(h) for h in headers]
        mapping = self.options.mapping

        def find(explicit: str | None, role: str, required: bool = False) -> int | None:
            if explicit:
                target = normalize_header(explicit)
                if target not in normalized:
                    raise ParseError(f"coluna {explicit!r} não encontrada. Colunas: {headers}")
                return normalized.index(target)
            for i, name in enumerate(normalized):
                if _SYNONYM_INDEX.get(name) == role:
                    return i
            if required:
                raise ParseError(f"coluna obrigatória ({role}) não encontrada. Colunas: {headers}")
            return None

        date_col = find(mapping.date, "date", required=True)
        assert date_col is not None
        if mapping.description:
            description = [
                c for c in (find(d, "description") for d in mapping.description) if c is not None
            ]
        else:
            found = find(None, "description")
            description = [found] if found is not None else []
        if not description:
            raise ParseError(f"coluna de descrição/histórico não encontrada. Colunas: {headers}")

        amount = find(mapping.amount, "amount")
        credit = find(mapping.credit, "credit")
        debit = find(mapping.debit, "debit")
        if amount is None and credit is None and debit is None:
            raise ParseError(
                f"coluna de valor (ou crédito/débito) não encontrada. Colunas: {headers}"
            )
        if mapping.amount is None and (mapping.credit or mapping.debit):
            amount = None  # crédito/débito explícitos prevalecem sobre o auto-detectado
        return _ResolvedColumns(
            date=date_col,
            description=description,
            amount=amount,
            credit=credit if amount is None else None,
            debit=debit if amount is None else None,
            document=find(mapping.document, "document"),
            dc_indicator=find(mapping.dc_indicator, "dc_indicator") if amount is not None else None,
        )

    # ------------------------------------------------------------------ linhas

    def _convert_row(
        self,
        line: int,
        row: list[Any],
        cols: _ResolvedColumns,
        skip_patterns: tuple[str, ...],
    ) -> RawTransaction | None:
        def cell(idx: int | None) -> Any:
            return None if idx is None or idx >= len(row) else row[idx]

        raw_date = cell(cols.date)
        description = " - ".join(
            str(cell(i)).strip() for i in cols.description if cell(i) is not None
        )
        value_cells = [cell(cols.amount), cell(cols.credit), cell(cols.debit)]
        if raw_date is None and all(v is None for v in value_cells):
            return None  # linha em branco / rodapé
        normalized_desc = normalize_text(description)
        if any(normalized_desc.startswith(p) for p in skip_patterns):
            return None
        if raw_date is None:
            raise RowParseError("data vazia")

        amount = self._resolve_amount(
            cell(cols.amount), cell(cols.credit), cell(cols.debit), cell(cols.dc_indicator)
        )
        if amount == 0:
            return None
        if self.options.invert_sign:
            amount = -amount
        document = cell(cols.document)
        return RawTransaction(
            date=self._parse_date(raw_date),
            description=description or "SEM HISTORICO",
            amount=amount,
            document=_clean_document(document),
            line=line,
        )

    @staticmethod
    def _resolve_amount(amount: Any, credit: Any, debit: Any, dc: Any) -> Decimal:
        if amount is not None:
            value = parse_amount(amount)
            if dc is not None:
                indicator = normalize_text(str(dc))[:1]
                if indicator == "D":
                    value = -abs(value)
                elif indicator == "C":
                    value = abs(value)
            return value
        credit_value = parse_amount(credit) if credit is not None else Decimal("0")
        debit_value = parse_amount(debit) if debit is not None else Decimal("0")
        if credit is None and debit is None:
            raise RowParseError("valor vazio")
        return abs(credit_value) - abs(debit_value)

    def _parse_date(self, raw: Any) -> date:
        if isinstance(raw, int | float) and not isinstance(raw, bool) and 20000 < raw < 80000:
            return _EXCEL_EPOCH + timedelta(days=int(raw))  # serial de data do Excel
        formats = (self.options.date_format,) if self.options.date_format else DATE_FORMATS
        return parse_date(raw, formats)


class CSVParser(TabularParser):
    source_format = SourceFormat.CSV

    def _read_grid(self, content: bytes) -> pd.DataFrame:
        text = decode_bytes(content)
        sample = text[:8192]
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        # csv.reader (não pd.read_csv) tolera preâmbulo com nº de campos diferente.
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
        width = max((len(r) for r in rows), default=0)
        return pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=object)


class ExcelParser(TabularParser):
    source_format = SourceFormat.XLSX

    def _read_grid(self, content: bytes) -> pd.DataFrame:
        try:
            frame = pd.read_excel(
                io.BytesIO(content), sheet_name=self.options.sheet_name, header=None, dtype=object
            )
        except (ValueError, KeyError, OSError) as exc:
            raise ParseError(f"não foi possível ler a planilha: {exc}") from exc
        if not isinstance(frame, pd.DataFrame):  # pragma: no cover - sheet_name=None não é usado
            raise ParseError("selecione uma única aba da planilha")
        return frame.reset_index(drop=True)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    if value is pd.NaT:
        return True
    return isinstance(value, str) and not value.strip()


def _clean_document(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text[:60] or None
