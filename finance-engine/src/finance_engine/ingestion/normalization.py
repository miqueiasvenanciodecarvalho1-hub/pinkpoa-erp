"""Normalização de valores no padrão brasileiro: decimais, datas, textos e encodings."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from finance_engine.core.exceptions import ParseError, RowParseError

_CENT = Decimal("0.01")
_AMOUNT_CLEAN_RE = re.compile(r"[^\d,.\-+]")
_WS_RE = re.compile(r"\s+")

DATE_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_CENT)


def parse_amount(raw: object) -> Decimal:
    """Converte valores monetários heterogêneos em ``Decimal`` com 2 casas.

    Aceita: ``1.234,56``, ``-1.234,56``, ``1,234.56``, ``R$ 1.234,56``, ``(1.234,56)``,
    ``1.234,56-``, ``1.234,56 D`` (débito), ``1.234,56 C`` e números nativos (int/float/Decimal).
    """
    if raw is None:
        raise RowParseError("valor vazio")
    if isinstance(raw, bool):
        raise RowParseError(f"valor inválido: {raw!r}")
    if isinstance(raw, Decimal):
        return quantize_money(raw)
    if isinstance(raw, int):
        return quantize_money(Decimal(raw))
    if isinstance(raw, float):
        if raw != raw:  # NaN
            raise RowParseError("valor vazio")
        # repr() evita artefatos binários (0.1 -> 0.1000000000000000055...).
        return quantize_money(Decimal(repr(raw)))

    text = str(raw).strip().upper()
    if not text:
        raise RowParseError("valor vazio")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("D") and not text.endswith("CD"):
        negative, text = True, text[:-1]
    elif text.endswith("C"):
        text = text[:-1]
    text = text.strip()
    if text.endswith("-"):
        negative, text = True, text[:-1]

    text = _AMOUNT_CLEAN_RE.sub("", text)
    if text.startswith("-"):
        negative, text = True, text[1:]
    text = text.lstrip("+")
    if not text or not any(ch.isdigit() for ch in text):
        raise RowParseError(f"valor inválido: {raw!r}")

    last_comma, last_dot = text.rfind(","), text.rfind(".")
    if last_comma > last_dot:
        # Padrão BR: ponto = milhar, vírgula = decimal.
        text = text.replace(".", "").replace(",", ".")
    elif last_dot > last_comma and last_comma != -1:
        # Padrão US: vírgula = milhar.
        text = text.replace(",", "")
    elif last_dot != -1 and last_comma == -1 and text.count(".") > 1:
        # "1.234.567" -> só separadores de milhar.
        text = text.replace(".", "")
    elif last_dot != -1 and last_comma == -1 and len(text) - last_dot - 1 == 3:
        # "1.234": no Brasil é milhar, não 1,234.
        text = text.replace(".", "")

    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise RowParseError(f"valor inválido: {raw!r}") from exc
    return quantize_money(-value if negative else value)


def parse_date(raw: object, formats: tuple[str, ...] = DATE_FORMATS) -> date:
    """Converte datas (texto em formatos BR/ISO, ``datetime``, ``pandas.Timestamp``)."""
    if raw is None:
        raise RowParseError("data vazia")
    if isinstance(raw, datetime):  # inclui pandas.Timestamp
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        raise RowParseError("data vazia")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise RowParseError(f"data inválida: {raw!r}")


def parse_compact_date(raw: str, *, year_digits: int = 4) -> date | None:
    """Datas CNAB: ``DDMMAAAA`` ou ``DDMMAA``. Zeros/brancos significam 'sem data'."""
    text = raw.strip()
    if not text or set(text) <= {"0"}:
        return None
    fmt = "%d%m%Y" if year_digits == 4 else "%d%m%y"
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError as exc:
        raise RowParseError(f"data CNAB inválida: {raw!r}") from exc


def normalize_text(text: str) -> str:
    """Maiúsculas, sem acentos e espaços colapsados — base para regras e deduplicação."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WS_RE.sub(" ", ascii_only).strip().upper()


def only_digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def decode_bytes(content: bytes, preferred: str | None = None) -> str:
    """Decodifica arquivos de bancos brasileiros (UTF-8 com/sem BOM, CP1252, Latin-1)."""
    candidates = [c for c in (preferred, "utf-8-sig", "cp1252", "latin-1") if c]
    for encoding in candidates:
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ParseError("não foi possível decodificar o arquivo")  # pragma: no cover (latin-1)
