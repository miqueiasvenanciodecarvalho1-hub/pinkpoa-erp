"""Parser OFX (1.x SGML e 2.x XML), tolerante às variações dos bancos brasileiros.

Implementado sem dependências externas: bibliotecas como ``ofxparse`` falham com frequência em
arquivos de bancos nacionais (tags SGML sem fechamento, CHARSET 1252, valores com vírgula,
fuso ``[-3:BRT]``). Aqui o arquivo é tokenizado tag a tag, o que cobre SGML e XML igualmente.
"""

from __future__ import annotations

import re
from datetime import date

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.base import ParseResult, RawTransaction, StatementParser
from finance_engine.ingestion.normalization import decode_bytes, parse_amount
from finance_engine.models.enums import SourceFormat

_TAG_RE = re.compile(r"<(/?)([A-Za-z0-9_.]+)>([^<]*)")
_CHARSET_RE = re.compile(r"CHARSET:\s*(\S+)|encoding=\"([^\"]+)\"", re.IGNORECASE)
_OFX_DATE_RE = re.compile(r"^(\d{8})")

# Tags-folha que nos interessam dentro de cada <STMTTRN>.
_TX_FIELDS = {"TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "CHECKNUM", "REFNUM", "MEMO", "NAME"}
_DEBIT_TYPES = {"DEBIT", "PAYMENT", "CHECK", "FEE", "SRVCHG", "ATM", "POS", "CASH", "DIRECTDEBIT"}


def _encoding_from_header(content: bytes) -> str | None:
    head = content[:600].decode("ascii", errors="ignore")
    match = _CHARSET_RE.search(head)
    if not match:
        return None
    value = (match.group(1) or match.group(2) or "").upper()
    if value in {"1252", "WINDOWS-1252"}:
        return "cp1252"
    if value in {"NONE", ""}:
        return None
    if value.startswith("ISO-8859") or value == "8859-1":
        return "latin-1"
    return value.lower()


def parse_ofx_date(raw: str) -> date:
    match = _OFX_DATE_RE.match(raw.strip())
    if not match:
        raise RowParseError(f"data OFX inválida: {raw!r}")
    digits = match.group(1)
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError as exc:
        raise RowParseError(f"data OFX inválida: {raw!r}") from exc


class OFXParser(StatementParser):
    source_format = SourceFormat.OFX

    def parse(self, content: bytes) -> ParseResult:
        text = decode_bytes(content, _encoding_from_header(content))
        start = text.upper().find("<OFX>")
        if start == -1:
            raise ParseError("arquivo OFX sem a tag <OFX>")
        body = text[start:]

        result = ParseResult(source_format=self.source_format)
        raw_rows: list[tuple[int, dict[str, str]]] = []
        current: dict[str, str] | None = None
        current_line = 0
        context: list[str] = []  # pilha simplificada para saber se estamos em LEDGERBAL etc.
        line_offset = text[:start].count("\n") + 1

        for match in _TAG_RE.finditer(body):
            closing, tag, value = match.group(1) == "/", match.group(2).upper(), match.group(3)
            value = value.strip()
            if tag == "STMTTRN":
                if closing:
                    if current is not None:
                        raw_rows.append((current_line, current))
                    current = None
                else:
                    current = {}
                    current_line = line_offset + body[: match.start()].count("\n")
                continue
            if closing:
                if tag in context:
                    while context and context.pop() != tag:
                        pass
                continue
            if not value:
                context.append(tag)  # agregado (tem filhos)
                continue

            if current is not None and tag in _TX_FIELDS:
                current[tag] = value
            elif tag == "BANKID":
                result.metadata.bank_code = value.zfill(3)[-3:]
            elif tag == "BRANCHID":
                result.metadata.branch = value
            elif tag == "ACCTID":
                result.metadata.account_number = value
            elif tag == "CURDEF":
                result.metadata.currency = value
            elif tag == "DTSTART":
                result.metadata.period_start = self._safe_date(value)
            elif tag == "DTEND":
                result.metadata.period_end = self._safe_date(value)
            elif "LEDGERBAL" in context and tag == "BALAMT":
                try:
                    result.metadata.balance = parse_amount(value)
                except RowParseError:
                    result.warnings.append(f"Saldo LEDGERBAL ilegível: {value!r}")
            elif "LEDGERBAL" in context and tag == "DTASOF":
                result.metadata.balance_date = self._safe_date(value)

        if not raw_rows:
            raise ParseError("nenhum <STMTTRN> encontrado no OFX")
        self._collect_rows(result, raw_rows, self._convert)
        return result

    @staticmethod
    def _safe_date(value: str) -> date | None:
        try:
            return parse_ofx_date(value)
        except RowParseError:
            return None

    @staticmethod
    def _convert(line: int, row: dict[str, str]) -> RawTransaction | None:
        if "DTPOSTED" not in row or "TRNAMT" not in row:
            raise RowParseError("STMTTRN sem DTPOSTED/TRNAMT")
        amount = parse_amount(row["TRNAMT"])
        if amount == 0:
            return None
        # Alguns bancos mandam débitos com valor positivo e TRNTYPE=DEBIT.
        if amount > 0 and row.get("TRNTYPE", "").upper() in _DEBIT_TYPES:
            amount = -amount
        name, memo = row.get("NAME", ""), row.get("MEMO", "")
        # Vários bancos repetem o mesmo texto em NAME e MEMO.
        parts = [name] if memo in ("", name) else [p for p in (name, memo) if p]
        description = " - ".join(parts) or "SEM HISTORICO"
        return RawTransaction(
            date=parse_ofx_date(row["DTPOSTED"]),
            description=description,
            amount=amount,
            document=row.get("CHECKNUM") or row.get("REFNUM"),
            external_id=row.get("FITID"),
            line=line,
        )
