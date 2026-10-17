"""CNAB 400 — retorno de cobrança (Bradesco, Itaú, Santander e similares).

O CNAB 400 não é padronizado pela FEBRABAN: cada banco tem seu layout. As posições abaixo são
as comuns a Bradesco e Itaú; diferenças conhecidas ficam em ``BANK_PROFILES``. Bancos sem perfil
usam o layout genérico e o resultado traz um aviso para conferência manual.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.base import ParseResult, RawTransaction, StatementParser
from finance_engine.ingestion.cnab.cnab240 import title_to_transaction
from finance_engine.ingestion.cnab.layout import FieldKind as K
from finance_engine.ingestion.cnab.layout import FieldSpec as F
from finance_engine.ingestion.cnab.layout import (
    RecordLayout,
    as_date,
    as_money,
    as_text,
    split_fixed_width_lines,
)
from finance_engine.ingestion.cnab.models import CnabFile, CnabFileKind, CnabHeader, CnabTitle
from finance_engine.models.enums import SourceFormat

WIDTH = 400

FILE_HEADER = RecordLayout(
    "header_arquivo",
    (
        F("operation", 2, 2, K.NUMBER),  # 1 = remessa, 2 = retorno
        F("bank_code", 77, 79, K.NUMBER),
        F("bank_name", 80, 94),
        F("generated_at", 95, 100, K.DATE6),
    ),
)

DETAIL = RecordLayout(
    "detalhe",
    (
        F("our_number", 71, 82),
        F("occurrence_code", 109, 110, K.NUMBER),
        F("occurrence_date", 111, 116, K.DATE6),
        F("document_number", 117, 126),
        F("due_date", 147, 152, K.DATE6),
        F("face_value", 153, 165, K.MONEY),
        F("fee", 176, 188, K.MONEY),
        F("discount", 241, 253, K.MONEY),
        F("paid_amount", 254, 266, K.MONEY),
        F("interest", 267, 279, K.MONEY),
        F("credit_date", 296, 301, K.DATE6),
    ),
)


@dataclass(frozen=True, slots=True)
class BankProfile:
    name: str
    overrides: tuple[F, ...] = ()
    liquidation_codes: frozenset[str] = frozenset({"06"})


BANK_PROFILES: dict[str, BankProfile] = {
    "237": BankProfile("Bradesco", liquidation_codes=frozenset({"06", "15", "17"})),
    "341": BankProfile(
        "Itaú",
        overrides=(F("our_number", 63, 70), F("payer_name", 325, 354)),
        liquidation_codes=frozenset({"06", "07", "08", "10"}),
    ),
    "033": BankProfile(
        "Santander",
        overrides=(F("our_number", 63, 70),),
        liquidation_codes=frozenset({"06", "07", "08"}),
    ),
}
GENERIC_PROFILE = BankProfile("Genérico", liquidation_codes=frozenset({"06"}))


class Cnab400Parser(StatementParser):
    source_format = SourceFormat.CNAB400

    def __init__(
        self, *, profile: BankProfile | None = None, max_invalid_ratio: float = 0.2
    ) -> None:
        super().__init__(max_invalid_ratio=max_invalid_ratio)
        self.forced_profile = profile

    def read(self, content: bytes) -> tuple[CnabFile, BankProfile]:
        lines = split_fixed_width_lines(content, WIDTH)
        first_line, first = lines[0]
        if first[0] != "0":
            raise ParseError("primeiro registro não é header de arquivo (tipo 0)", line=first_line)
        h = FILE_HEADER.parse(first)
        bank_code = as_text(h["bank_code"])
        header = CnabHeader(
            bank_code=bank_code,
            bank_name=as_text(h["bank_name"]),
            kind=CnabFileKind.RETORNO if h["operation"] == "2" else CnabFileKind.REMESSA,
            generated_at=as_date(h["generated_at"]),
        )
        profile = self.forced_profile or BANK_PROFILES.get(bank_code, GENERIC_PROFILE)
        cnab = CnabFile(header=header, titles=[])
        if profile is GENERIC_PROFILE:
            cnab.warnings.append(
                f"Banco {bank_code} sem perfil CNAB 400 validado; confira os valores importados."
            )
        detail = DETAIL.with_overrides(*profile.overrides)

        for number, line in lines[1:]:
            if line[0] != "1":
                continue  # 9 = trailer; outros tipos (2, 4, 5...) são opcionais por banco
            try:
                d = detail.parse(line)
                cnab.titles.append(
                    CnabTitle(
                        line=number,
                        occurrence_code=as_text(d["occurrence_code"]),
                        our_number=as_text(d["our_number"]),
                        document_number=as_text(d["document_number"]),
                        due_date=as_date(d["due_date"]),
                        face_value=as_money(d["face_value"]),
                        paid_amount=as_money(d["paid_amount"]),
                        interest=as_money(d["interest"]),
                        discount=as_money(d["discount"]),
                        fee=as_money(d["fee"]),
                        occurrence_date=as_date(d["occurrence_date"]),
                        credit_date=as_date(d["credit_date"]),
                        payer_name=as_text(d.get("payer_name")),
                    )
                )
            except RowParseError as exc:
                cnab.warnings.append(f"Linha {number}: {exc}")
        return cnab, profile

    def parse(self, content: bytes) -> ParseResult:
        cnab, profile = self.read(content)
        if cnab.header.kind is CnabFileKind.REMESSA:
            raise ParseError(
                "arquivo de REMESSA não gera lançamentos realizados; importe o arquivo de RETORNO"
            )
        result = ParseResult(source_format=self.source_format, warnings=list(cnab.warnings))
        result.metadata.bank_code = cnab.header.bank_code

        def convert(line: int, title: CnabTitle) -> RawTransaction | None:
            # O CNAB 400 não traz o líquido: crédito = valor pago - tarifa de cobrança.
            if not title.net_credited and title.paid_amount > title.fee:
                title.net_credited = title.paid_amount - title.fee
            return title_to_transaction(line, title, profile.liquidation_codes)

        self._collect_rows(result, ((t.line, t) for t in cnab.titles), convert)
        return result
