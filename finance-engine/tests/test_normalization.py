from datetime import date
from decimal import Decimal

import pytest

from finance_engine.core.exceptions import RowParseError
from finance_engine.ingestion.detection import detect_format
from finance_engine.ingestion.normalization import normalize_text, parse_amount, parse_date
from finance_engine.models import SourceFormat
from tests import samples


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", "1234.56"),
        ("-1.234,56", "-1234.56"),
        ("1,234.56", "1234.56"),
        ("R$ 1.234,56", "1234.56"),
        ("(1.234,56)", "-1234.56"),
        ("1.234,56-", "-1234.56"),
        ("1.234,56 D", "-1234.56"),
        ("10,00 C", "10.00"),
        ("1.234", "1234.00"),
        ("1.234.567", "1234567.00"),
        ("123.45", "123.45"),
        (-5.5, "-5.50"),
        (0.1, "0.10"),
        (Decimal("3.456"), "3.46"),
    ],
)
def test_parse_amount(raw: object, expected: str) -> None:
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "abc", None, float("nan"), True])
def test_parse_amount_invalid(raw: object) -> None:
    with pytest.raises(RowParseError):
        parse_amount(raw)


def test_parse_date_formats() -> None:
    assert parse_date("05/02/2024") == date(2024, 2, 5)
    assert parse_date("05/02/24") == date(2024, 2, 5)
    assert parse_date("2024-02-05") == date(2024, 2, 5)
    with pytest.raises(RowParseError):
        parse_date("31/02/2024")


def test_normalize_text() -> None:
    assert normalize_text("  Pagamento  Ação\tEducação ") == "PAGAMENTO ACAO EDUCACAO"


def test_detect_format() -> None:
    assert detect_format("x.ofx", samples.ofx_bytes()) is SourceFormat.OFX
    assert detect_format("x.csv", samples.csv_bytes()) is SourceFormat.CSV
    assert detect_format("x.xlsx", samples.xlsx_bytes()) is SourceFormat.XLSX
    assert detect_format("x.ret", samples.cnab240_retorno()) is SourceFormat.CNAB240
    assert detect_format("x.ret", samples.cnab400_retorno_bradesco()) is SourceFormat.CNAB400
