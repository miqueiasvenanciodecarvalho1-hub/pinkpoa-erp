"""Fábrica de parsers: ponto único de extensão para novos formatos."""

from __future__ import annotations

from dataclasses import dataclass, field

from finance_engine.core.exceptions import UnsupportedFormatError
from finance_engine.ingestion.base import StatementParser
from finance_engine.ingestion.cnab import Cnab240Parser, Cnab400Parser
from finance_engine.ingestion.ofx import OFXParser
from finance_engine.ingestion.tabular import CSVParser, ExcelParser, TabularOptions
from finance_engine.models.enums import SourceFormat


@dataclass(slots=True)
class ParserOptions:
    tabular: TabularOptions = field(default_factory=TabularOptions)
    max_invalid_ratio: float = 0.2


def get_parser(
    source_format: SourceFormat, options: ParserOptions | None = None
) -> StatementParser:
    opts = options or ParserOptions()
    ratio = opts.max_invalid_ratio
    match source_format:
        case SourceFormat.OFX:
            return OFXParser(max_invalid_ratio=ratio)
        case SourceFormat.CSV:
            return CSVParser(opts.tabular, max_invalid_ratio=ratio)
        case SourceFormat.XLSX:
            return ExcelParser(opts.tabular, max_invalid_ratio=ratio)
        case SourceFormat.CNAB240:
            return Cnab240Parser(max_invalid_ratio=ratio)
        case SourceFormat.CNAB400:
            return Cnab400Parser(max_invalid_ratio=ratio)
        case _:
            raise UnsupportedFormatError(f"sem parser para {source_format}")
