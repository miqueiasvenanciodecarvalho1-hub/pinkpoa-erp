"""Leitura de demonstrativos contábeis (balanço, DRE, balancete) para linhas padronizáveis.

Ordem de preferência — do mais confiável ao menos:
1. **SPED ECD** (TXT entregue à Receita): registros J100 (balanço) e J150 (DRE), estruturados.
2. **Planilha** (CSV/XLSX) com descrição e valor.
3. **PDF com texto**: linhas "Descrição .... 1.234,56" extraídas com pypdf.
4. **PDF via IA** (Claude, ChatGPT ou Gemini, conforme configuração): para PDFs escaneados ou
   layouts difíceis; devolve linhas já classificadas.

O resultado é sempre um rascunho: o consultor revisa antes de validar (ver ``StatementStatus``).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from finance_engine.ai.providers import AIProvider, AIProviderError
from finance_engine.analysis.chart_of_accounts import STANDARD_ACCOUNTS, StatementLine
from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.normalization import decode_bytes, parse_amount
from finance_engine.ingestion.tabular import CSVParser, ExcelParser
from finance_engine.models.enums import StatementKind


@dataclass(slots=True)
class ExtractedStatement:
    kind: StatementKind
    lines: list[StatementLine]
    period_end: date | None = None
    period_start: date | None = None
    source: str = ""
    preset_codes: dict[str, Decimal] = field(default_factory=dict)  # quando a IA já classifica
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- SPED ECD

_NUM_DC = re.compile(r"^\d+(,\d+)?$")


def _ecd_value(fields: list[str], newest_layout: bool) -> Decimal | None:
    pairs: list[tuple[str, str]] = []
    for i in range(len(fields) - 1):
        if _NUM_DC.match(fields[i]) and fields[i + 1] in ("D", "C", "R", "P", "N"):
            pairs.append((fields[i], fields[i + 1]))
    if not pairs:
        return None
    value, _ = pairs[-1] if newest_layout else pairs[0]
    return Decimal(value.replace(",", "."))


def parse_ecd(content: bytes) -> list[ExtractedStatement]:
    text = decode_bytes(content)
    period_end: date | None = None
    period_start: date | None = None
    bal: list[StatementLine] = []
    dre: list[StatementLine] = []
    for raw in text.splitlines():
        if not raw.startswith("|"):
            continue
        f = raw.strip().strip("|").split("|")
        reg = f[0]
        if reg == "0000" and len(f) > 3:
            for v in f[1:4]:
                if re.fullmatch(r"\d{8}", v):
                    d = datetime.strptime(v, "%d%m%Y").date()
                    period_start = period_start or d
                    period_end = d
        elif reg in ("J100", "J150"):
            # layout novo (2019+) tem indicador T/D logo após o código de aglutinação
            newest = (
                len(f) > 3 and f[2] in ("T", "D")
                if reg == "J100"
                else (len(f) > 4 and f[3] in ("T", "D"))
            )
            desc = next((x for x in f[1:] if re.search(r"[A-Za-zÀ-ú]{3,}", x)), "")
            level = next((int(x) for x in f[1:6] if x.isdigit() and len(x) == 1), None)
            value = _ecd_value(f[1:], newest)
            if desc and value is not None:
                (bal if reg == "J100" else dre).append(StatementLine(desc, value, level))
    out = []
    if bal:
        out.append(
            ExtractedStatement(
                StatementKind.BALANCE_SHEET, bal, period_end, period_start, "SPED ECD (J100)"
            )
        )
    if dre:
        out.append(
            ExtractedStatement(
                StatementKind.INCOME_STATEMENT, dre, period_end, period_start, "SPED ECD (J150)"
            )
        )
    if not out:
        raise ParseError("arquivo ECD sem registros J100/J150")
    return out


# ----------------------------------------------------------------------------- planilha


def parse_statement_table(content: bytes, filename: str) -> list[StatementLine]:
    parser = ExcelParser() if content[:2] == b"PK" else CSVParser()
    grid = parser._read_grid(content)
    lines: list[StatementLine] = []
    for row in grid.itertuples(index=False, name=None):
        cells = [c for c in row if c is not None and str(c).strip() not in ("", "nan")]
        desc = next(
            (str(c) for c in cells if isinstance(c, str) and re.search(r"[A-Za-zÀ-ú]{3,}", c)), None
        )
        if not desc:
            continue
        value = None
        for c in reversed(cells):
            try:
                value = parse_amount(c)
                break
            except RowParseError:
                continue
        if value is not None:
            lines.append(StatementLine(desc.strip(), value))
    if not lines:
        raise ParseError(f"{filename}: nenhuma linha com descrição e valor")
    return lines


# ----------------------------------------------------------------------------- PDF (texto)

_PDF_LINE = re.compile(
    r"^(?P<desc>.*?[A-Za-zÀ-ú].*?)[\s\.]{2,}(?P<vals>\(?-?[\d\.]+,\d{2}\)?(?:\s+\(?-?[\d\.]+,\d{2}\)?)*)\s*$"
)


def pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ParseError("leitura de PDF requer o pacote pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ParseError(f"PDF ilegível: {exc}") from exc


def lines_from_text(text: str, column: int = 0) -> list[StatementLine]:
    """``column``: 0 = primeira coluna de valores (em geral o exercício atual)."""
    out: list[StatementLine] = []
    for raw in text.splitlines():
        m = _PDF_LINE.match(raw.strip())
        if not m:
            continue
        vals = m.group("vals").split()
        try:
            out.append(
                StatementLine(
                    m.group("desc").strip(" .:"), parse_amount(vals[min(column, len(vals) - 1)])
                )
            )
        except RowParseError:
            continue
    return out


def guess_kind(lines: list[StatementLine]) -> StatementKind:
    text = " ".join(line.description.upper() for line in lines[:80])
    bal = sum(w in text for w in ("ATIVO", "PASSIVO", "PATRIMONIO", "PATRIMÔNIO"))
    dre = sum(w in text for w in ("RECEITA", "CUSTO", "LUCRO", "DESPESAS"))
    return StatementKind.BALANCE_SHEET if bal >= dre else StatementKind.INCOME_STATEMENT


# ----------------------------------------------------------------------------- PDF via IA


class StatementLLM(Protocol):
    def extract(self, pdf: bytes) -> dict[str, Any]: ...


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["BALANCE_SHEET", "INCOME_STATEMENT", "TRIAL_BALANCE"]},
        "company": {"type": "string"},
        "period_end": {"type": "string", "description": "AAAA-MM-DD"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "value": {
                        "type": "number",
                        "description": "valor do período mais recente, positivo",
                    },
                    "code": {"type": "string", "enum": [*STANDARD_ACCOUNTS.keys(), "OTHER"]},
                },
                "required": ["description", "value", "code"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["kind", "company", "period_end", "lines"],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = (
    "Você extrai demonstrativos contábeis brasileiros (balanço patrimonial, DRE ou balancete). "
    "Leia o PDF e devolva cada linha com a descrição como aparece, o valor do período mais recente "
    "(sempre positivo, sem separador de milhar) e o código padrão mais adequado. Use OTHER quando "
    "nenhum código servir. Totais de grupo (ex.: Ativo Circulante) recebem o código do total. "
    "Códigos disponíveis:\n" + "\n".join(f"- {k}: {v}" for k, v in STANDARD_ACCOUNTS.items())
)


class AIStatementExtractor:
    """Extração por IA com qualquer fornecedor (Claude, ChatGPT, Gemini): ``finance_engine.ai``."""

    def __init__(self, provider: AIProvider) -> None:
        self.provider = provider

    def extract(self, pdf: bytes) -> dict[str, Any]:
        try:
            return self.provider.extract_json(EXTRACTION_PROMPT, EXTRACTION_SCHEMA, pdf=pdf)
        except AIProviderError as exc:
            raise ParseError(str(exc)) from exc


def statement_from_llm(data: dict[str, Any]) -> ExtractedStatement:
    lines = [
        StatementLine(str(x["description"]), Decimal(str(x["value"])))
        for x in data.get("lines", [])
    ]
    preset: dict[str, Decimal] = {}
    for x in data.get("lines", []):
        code = x.get("code")
        if code in STANDARD_ACCOUNTS and code not in preset:
            preset[code] = abs(Decimal(str(x["value"])))
    try:
        period_end = date.fromisoformat(str(data.get("period_end"))[:10])
    except ValueError:
        period_end = None
    return ExtractedStatement(
        StatementKind(data["kind"]), lines, period_end, None, "PDF via IA", preset
    )


def extract_statements(
    content: bytes, filename: str, *, llm: StatementLLM | None = None, use_ai: bool = False
) -> list[ExtractedStatement]:
    name = filename.lower()
    head = content[:4000].decode("latin-1", errors="ignore")
    if "|0000|LECD|" in head or "|J100|" in decode_bytes(content)[:200000]:
        return parse_ecd(content)
    if name.endswith((".csv", ".xlsx", ".xls")) or content[:2] == b"PK":
        lines = parse_statement_table(content, filename)
        return [ExtractedStatement(guess_kind(lines), lines, source="Planilha")]
    if content[:4] == b"%PDF":
        if use_ai and llm is not None:
            return [statement_from_llm(llm.extract(content))]
        lines = lines_from_text(pdf_text(content))
        if len(lines) < 5:
            if llm is not None:
                return [statement_from_llm(llm.extract(content))]
            raise ParseError(
                "PDF sem texto legível (provavelmente escaneado): use a extração por IA"
            )
        return [ExtractedStatement(guess_kind(lines), lines, source="PDF (texto)")]
    raise ParseError("formato não reconhecido: envie SPED ECD (.txt), planilha ou PDF")
