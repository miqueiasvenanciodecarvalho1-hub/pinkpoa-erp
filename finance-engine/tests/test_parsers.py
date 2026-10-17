from datetime import date
from decimal import Decimal

import pytest

from finance_engine.core.exceptions import ParseError
from finance_engine.ingestion import ColumnMapping, TabularOptions
from finance_engine.ingestion.cnab import Cnab240Parser, Cnab400Parser
from finance_engine.ingestion.ofx import OFXParser
from finance_engine.ingestion.tabular import CSVParser, ExcelParser
from tests import samples


def test_ofx_sgml_brazilian_quirks() -> None:
    result = OFXParser().parse(samples.ofx_bytes())
    txs = result.transactions
    assert len(txs) == 6
    assert result.metadata.bank_code == "341"
    assert result.metadata.account_number == "56789-0"
    assert result.metadata.balance == Decimal("10209.35")
    assert result.metadata.balance_date == date(2024, 1, 31)
    # vírgula decimal
    assert txs[1].amount == Decimal("-4500.00")
    # NAME == MEMO não duplica o histórico
    assert txs[1].description == "FORNECEDOR X LTDA"
    assert txs[1].document == "991"
    # débito com valor positivo + TRNTYPE=DEBIT vira negativo
    assert txs[2].amount == Decimal("-890.15")
    # CP1252 preservado
    assert txs[5].description == "Pagamento Ação Educação"
    assert txs[0].external_id == "A1"


def test_ofx_without_transactions() -> None:
    with pytest.raises(ParseError):
        OFXParser().parse(b"OFXHEADER:100\n<OFX><BANKMSGSRSV1></BANKMSGSRSV1></OFX>")


def test_csv_with_preamble_and_balance_lines() -> None:
    result = CSVParser().parse(samples.csv_bytes())
    assert [t.amount for t in result.transactions] == [
        Decimal("2500.00"),
        Decimal("-0.50"),
        Decimal("-0.50"),
        Decimal("-1234.56"),
    ]
    assert result.transactions[0].document == "123"
    assert result.skipped_rows == 3  # saldo anterior, linha quebrada, saldo do dia
    assert any("LINHA" in w or "data inválida" in w for w in result.warnings)


def test_csv_credit_debit_columns() -> None:
    content = (
        "Data,Descrição,Crédito,Débito\n"
        "01/03/2024,VENDA BALCAO,150.00,\n"
        "02/03/2024,COMPRA INSUMOS,,80.25\n"
    ).encode()
    result = CSVParser().parse(content)
    assert [t.amount for t in result.transactions] == [Decimal("150.00"), Decimal("-80.25")]


def test_csv_too_many_invalid_rows() -> None:
    content = b"Data;Historico;Valor\naa;X;1,00\nbb;Y;2,00\n01/01/2024;Z;3,00\n"
    with pytest.raises(ParseError, match="linhas inválidas"):
        CSVParser().parse(content)


def test_xlsx_explicit_mapping_and_dc_column() -> None:
    options = TabularOptions(
        mapping=ColumnMapping(
            date="Dt. Movimento",
            description=["Descrição do lançamento"],
            amount="Montante",
            dc_indicator="D/C",
            document="Nº Doc",
        ),
        sheet_name="Extrato",
    )
    result = ExcelParser(options).parse(samples.xlsx_bytes())
    assert [(t.date, t.amount, t.document) for t in result.transactions] == [
        (date(2024, 3, 1), Decimal("-1500.00"), "1001"),
        (date(2024, 3, 4), Decimal("9876.54"), None),
        (date(2024, 3, 6), Decimal("-7000.00"), "1003"),
    ]


def test_xlsx_unknown_column_in_mapping() -> None:
    options = TabularOptions(mapping=ColumnMapping(date="Inexistente"), sheet_name="Extrato")
    with pytest.raises(ParseError, match="cabeçalho não encontrado"):
        ExcelParser(options).parse(samples.xlsx_bytes())


def test_cnab240_retorno_only_liquidations() -> None:
    parser = Cnab240Parser()
    cnab = parser.read(samples.cnab240_retorno())
    assert cnab.header.bank_code == "341"
    assert len(cnab.titles) == 2
    title = cnab.titles[0]
    assert title.face_value == Decimal("1000.00")
    assert title.fee == Decimal("3.50")
    assert title.paid_amount == Decimal("1010.00")

    result = parser.parse(samples.cnab240_retorno())
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.amount == Decimal("1006.50")  # líquido creditado
    assert tx.date == date(2024, 2, 5)  # data de crédito
    assert "CLIENTE GAMA" in tx.description
    assert result.skipped_rows == 1


def test_cnab400_bradesco() -> None:
    result = Cnab400Parser().parse(samples.cnab400_retorno_bradesco())
    assert result.metadata.bank_code == "237"
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.amount == Decimal("1997.10")  # pago - tarifa
    assert tx.date == date(2024, 2, 9)
    assert tx.document == "DUP-55"
    assert not result.warnings  # banco com perfil validado


def test_cnab_remessa_rejected() -> None:
    content = samples.cnab240_retorno().replace(b"BANCO ITAU SA", b"BANCO ITAU SA", 1)
    remessa = bytearray(content)
    remessa[142] = ord("1")  # posição 143 = 1 (remessa)
    with pytest.raises(ParseError, match="REMESSA"):
        Cnab240Parser().parse(bytes(remessa))
