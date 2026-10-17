"""CNAB 240 (FEBRABAN) — cobrança, segmentos T e U do retorno.

Estrutura do arquivo:
    0 Header de arquivo | 1 Header de lote | 3 Detalhe (segmentos P/Q remessa, T/U retorno)
    5 Trailer de lote   | 9 Trailer de arquivo
Posições conforme o layout padrão FEBRABAN v10.x. Cada banco publica ajustes pontuais —
use ``Cnab240Parser(overrides_t=..., overrides_u=...)`` para acomodá-los sem duplicar o layout.
"""

from __future__ import annotations

from decimal import Decimal

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.base import ParseResult, RawTransaction, StatementParser
from finance_engine.ingestion.cnab.layout import (
    FieldKind as K,
)
from finance_engine.ingestion.cnab.layout import (
    FieldSpec as F,
)
from finance_engine.ingestion.cnab.layout import (
    RecordLayout,
    as_date,
    as_money,
    as_text,
    split_fixed_width_lines,
)
from finance_engine.ingestion.cnab.models import CnabFile, CnabFileKind, CnabHeader, CnabTitle
from finance_engine.models.enums import SourceFormat

WIDTH = 240

FILE_HEADER = RecordLayout(
    "header_arquivo",
    (
        F("bank_code", 1, 3, K.NUMBER),
        F("company_doc_type", 18, 18, K.NUMBER),
        F("company_document", 19, 32, K.NUMBER),
        F("branch", 53, 57, K.NUMBER),
        F("account_number", 59, 70, K.NUMBER),
        F("company_name", 73, 102),
        F("bank_name", 103, 132),
        F("file_kind", 143, 143, K.NUMBER),  # 1 = remessa, 2 = retorno
        F("generated_at", 144, 151, K.DATE8),
    ),
)

SEGMENT_T = RecordLayout(
    "segmento_t",
    (
        F("occurrence_code", 16, 17, K.NUMBER),
        F("our_number", 38, 57),
        F("wallet", 58, 58, K.NUMBER),
        F("document_number", 59, 73),
        F("due_date", 74, 81, K.DATE8),
        F("face_value", 82, 96, K.MONEY),
        F("payer_doc_type", 133, 133, K.NUMBER),
        F("payer_document", 134, 148, K.NUMBER),
        F("payer_name", 149, 188),
        F("fee", 199, 213, K.MONEY),
        F("rejection_reasons", 214, 223),
    ),
)

SEGMENT_U = RecordLayout(
    "segmento_u",
    (
        F("occurrence_code", 16, 17, K.NUMBER),
        F("interest", 18, 32, K.MONEY),
        F("discount", 33, 47, K.MONEY),
        F("rebate", 48, 62, K.MONEY),
        F("iof", 63, 77, K.MONEY),
        F("paid_amount", 78, 92, K.MONEY),
        F("net_credited", 93, 107, K.MONEY),
        F("other_expenses", 108, 122, K.MONEY),
        F("other_credits", 123, 137, K.MONEY),
        F("occurrence_date", 138, 145, K.DATE8),
        F("credit_date", 146, 153, K.DATE8),
    ),
)

# Códigos de movimento de retorno FEBRABAN que representam entrada de dinheiro.
LIQUIDATION_CODES_240 = frozenset({"06", "17"})


class Cnab240Parser(StatementParser):
    source_format = SourceFormat.CNAB240

    def __init__(
        self,
        *,
        overrides_t: tuple[F, ...] = (),
        overrides_u: tuple[F, ...] = (),
        liquidation_codes: frozenset[str] = LIQUIDATION_CODES_240,
        max_invalid_ratio: float = 0.2,
    ) -> None:
        super().__init__(max_invalid_ratio=max_invalid_ratio)
        self.segment_t = SEGMENT_T.with_overrides(*overrides_t)
        self.segment_u = SEGMENT_U.with_overrides(*overrides_u)
        self.liquidation_codes = liquidation_codes

    def read(self, content: bytes) -> CnabFile:
        lines = split_fixed_width_lines(content, WIDTH)
        first_line, first = lines[0]
        if first[7] != "0":
            raise ParseError("primeiro registro não é header de arquivo (tipo 0)", line=first_line)
        h = FILE_HEADER.parse(first)
        header = CnabHeader(
            bank_code=as_text(h["bank_code"]),
            bank_name=as_text(h["bank_name"]),
            kind=CnabFileKind.RETORNO if h["file_kind"] == "2" else CnabFileKind.REMESSA,
            generated_at=as_date(h["generated_at"]),
            company_document=as_text(h["company_document"]) or None,
            branch=as_text(h["branch"]) or None,
            account_number=as_text(h["account_number"]) or None,
        )
        cnab = CnabFile(header=header, titles=[])
        pending: CnabTitle | None = None

        for number, line in lines[1:]:
            record_type, segment = line[7], line[13]
            if record_type != "3":
                continue  # headers/trailers de lote e trailer de arquivo
            try:
                if segment == "T":
                    t = self.segment_t.parse(line)
                    pending = CnabTitle(
                        line=number,
                        occurrence_code=as_text(t["occurrence_code"]),
                        our_number=as_text(t["our_number"]),
                        document_number=as_text(t["document_number"]),
                        due_date=as_date(t["due_date"]),
                        face_value=as_money(t["face_value"]),
                        fee=as_money(t["fee"]),
                        payer_name=as_text(t["payer_name"]),
                    )
                    cnab.titles.append(pending)
                elif segment == "U":
                    if pending is None:
                        raise RowParseError("segmento U sem segmento T anterior")
                    u = self.segment_u.parse(line)
                    pending.paid_amount = as_money(u["paid_amount"])
                    pending.net_credited = as_money(u["net_credited"])
                    pending.interest = as_money(u["interest"])
                    pending.discount = as_money(u["discount"])
                    pending.occurrence_date = as_date(u["occurrence_date"])
                    pending.credit_date = as_date(u["credit_date"])
                    pending = None
            except RowParseError as exc:
                cnab.warnings.append(f"Linha {number}: {exc}")
        return cnab

    def parse(self, content: bytes) -> ParseResult:
        cnab = self.read(content)
        if cnab.header.kind is CnabFileKind.REMESSA:
            raise ParseError(
                "arquivo de REMESSA não gera lançamentos realizados; importe o arquivo de RETORNO"
            )
        result = ParseResult(source_format=self.source_format, warnings=list(cnab.warnings))
        result.metadata.bank_code = cnab.header.bank_code
        result.metadata.branch = cnab.header.branch
        result.metadata.account_number = cnab.header.account_number
        self._collect_rows(result, ((t.line, t) for t in cnab.titles), self._convert)
        return result

    def _convert(self, line: int, title: CnabTitle) -> RawTransaction | None:
        return title_to_transaction(line, title, self.liquidation_codes)


def title_to_transaction(
    line: int, title: CnabTitle, liquidation_codes: frozenset[str]
) -> RawTransaction | None:
    """Liquidação vira receita pelo valor líquido creditado (o que de fato cai na conta)."""
    if title.occurrence_code not in liquidation_codes:
        return None  # entrada confirmada, baixa, rejeição... não movimentam caixa
    amount = title.net_credited or title.paid_amount
    if amount <= Decimal("0"):
        raise RowParseError(f"liquidação sem valor pago (nosso número {title.our_number})")
    when = title.credit_date or title.occurrence_date
    if when is None:
        raise RowParseError(f"liquidação sem data (nosso número {title.our_number})")
    payer = f" {title.payer_name}" if title.payer_name else ""
    return RawTransaction(
        date=when,
        description=(
            f"LIQUIDACAO COBRANCA{payer} DOC {title.document_number}".strip()
            + f" (TITULO {title.face_value} PAGO {title.paid_amount})"
        ),
        amount=amount,
        document=title.document_number or None,
        external_id=f"CNAB:{title.our_number}:{title.occurrence_code}:{when.isoformat()}",
        line=line,
    )
