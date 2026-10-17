"""Geradores de arquivos de exemplo (OFX, CSV, XLSX, CNAB) usados nos testes."""

from __future__ import annotations

import io
from datetime import date

import pandas as pd

OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20240131120000[-3:BRT]<LANGUAGE>POR</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>BRL
<BANKACCTFROM><BANKID>0341<BRANCHID>1234<ACCTID>56789-0<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20240101<DTEND>20240131
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20240102120000[-3:BRT]<TRNAMT>15000.00<FITID>A1<MEMO>PIX RECEBIDO CLIENTE ALFA</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20240105<TRNAMT>-4500,00<FITID>A2<CHECKNUM>991<NAME>FORNECEDOR X LTDA<MEMO>FORNECEDOR X LTDA</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20240110<TRNAMT>890.15<FITID>A3<MEMO>CONTA DE ENERGIA CEMIG</STMTTRN>
<STMTTRN><TRNTYPE>FEE<DTPOSTED>20240110<TRNAMT>-0.50<FITID>A4<MEMO>TARIFA PIX</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20240115<TRNAMT>-3200.00<FITID>A5<MEMO>ALUGUEL SALA COMERCIAL</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20240120<TRNAMT>-1200.00<FITID>A6<MEMO>Pagamento Ação Educação</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>10209.35<DTASOF>20240131</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def ofx_bytes() -> bytes:
    return OFX_SGML.encode("cp1252")


CSV_STATEMENT = """Banco Exemplo S.A.;;;;
Extrato de conta corrente;;;;
Agência: 1234 Conta: 56789-0;;;;
;;;;
Data;Histórico;Documento;Valor (R$);Saldo (R$)
31/01/2024;SALDO ANTERIOR;;;10.209,35
01/02/2024;PIX RECEBIDO CLIENTE BETA;123;2.500,00;12.709,35
02/02/2024;TARIFA PIX;;-0,50;12.708,85
02/02/2024;TARIFA PIX;;-0,50;12.708,35
05/02/2024;PAGTO FORNECEDOR X;77;-1.234,56;11.473,79
xx/02/2024;LINHA QUEBRADA;;-1,00;
10/02/2024;SALDO DO DIA;;;11.473,79
"""


def csv_bytes() -> bytes:
    return CSV_STATEMENT.encode("cp1252")


def xlsx_bytes() -> bytes:
    frame = pd.DataFrame(
        {
            "Dt. Movimento": [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 6)],
            "Descrição do lançamento": ["DARF IRPJ", "RECEBIMENTO CIELO", "FOLHA PAGAMENTO"],
            "Montante": [1500.0, 9876.54, 7000.0],
            "D/C": ["D", "C", "D"],
            "Nº Doc": [1001.0, None, 1003.0],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([["Relatório gerencial"], [""]]).to_excel(
            writer, index=False, header=False, sheet_name="Extrato"
        )
        frame.to_excel(writer, index=False, startrow=3, sheet_name="Extrato")
    return buffer.getvalue()


class FixedWidth:
    """Monta registros CNAB por posição (1-based, inclusiva), como nos manuais."""

    def __init__(self, width: int) -> None:
        self.chars = [" "] * width

    def put(self, start: int, end: int, value: str | int, *, numeric: bool = False) -> FixedWidth:
        size = end - start + 1
        text = str(value)
        text = text.zfill(size) if numeric else text.ljust(size)
        assert len(text) == size, (start, end, value)
        self.chars[start - 1 : end] = list(text)
        return self

    def __str__(self) -> str:
        return "".join(self.chars)


def cents(value: str) -> str:
    return value.replace(".", "")


def cnab240_retorno() -> bytes:
    header = FixedWidth(240).put(1, 3, "341").put(4, 7, "0000").put(8, 8, "0")
    header.put(103, 132, "BANCO ITAU SA").put(143, 143, "2").put(144, 151, "05022024")
    lote = FixedWidth(240).put(1, 3, "341").put(4, 7, "0001").put(8, 8, "1")
    t1 = FixedWidth(240).put(1, 3, "341").put(8, 8, "3").put(14, 14, "T").put(16, 17, "06")
    t1.put(38, 57, "00000012345").put(59, 73, "NF-1001").put(74, 81, "31012024")
    t1.put(82, 96, cents("1000.00"), numeric=True).put(149, 188, "CLIENTE GAMA LTDA")
    t1.put(199, 213, cents("3.50"), numeric=True)
    u1 = FixedWidth(240).put(1, 3, "341").put(8, 8, "3").put(14, 14, "U").put(16, 17, "06")
    u1.put(18, 32, cents("10.00"), numeric=True).put(78, 92, cents("1010.00"), numeric=True)
    u1.put(93, 107, cents("1006.50"), numeric=True).put(138, 145, "02022024")
    u1.put(146, 153, "05022024")
    # Entrada confirmada (02): não movimenta caixa.
    t2 = FixedWidth(240).put(1, 3, "341").put(8, 8, "3").put(14, 14, "T").put(16, 17, "02")
    t2.put(38, 57, "00000012346").put(59, 73, "NF-1002").put(74, 81, "28022024")
    t2.put(82, 96, cents("500.00"), numeric=True)
    u2 = FixedWidth(240).put(1, 3, "341").put(8, 8, "3").put(14, 14, "U").put(16, 17, "02")
    trailer_lote = FixedWidth(240).put(1, 3, "341").put(8, 8, "5")
    trailer = FixedWidth(240).put(1, 3, "341").put(8, 8, "9")
    lines = [header, lote, t1, u1, t2, u2, trailer_lote, trailer]
    return "\r\n".join(str(x) for x in lines).encode("latin-1") + b"\r\n"


def cnab400_retorno_bradesco() -> bytes:
    header = FixedWidth(400).put(1, 1, "0").put(2, 2, "2").put(77, 79, "237")
    header.put(80, 94, "BRADESCO").put(95, 100, "100224")
    d1 = FixedWidth(400).put(1, 1, "1").put(71, 82, "000000099991").put(109, 110, "06")
    d1.put(111, 116, "080224").put(117, 126, "DUP-55").put(147, 152, "070224")
    d1.put(153, 165, cents("2000.00"), numeric=True).put(176, 188, cents("2.90"), numeric=True)
    d1.put(254, 266, cents("2000.00"), numeric=True).put(296, 301, "090224")
    d2 = FixedWidth(400).put(1, 1, "1").put(71, 82, "000000099992").put(109, 110, "09")
    d2.put(153, 165, cents("300.00"), numeric=True)
    trailer = FixedWidth(400).put(1, 1, "9")
    return "\n".join(str(x) for x in (header, d1, d2, trailer)).encode("latin-1")
