"""Importação de contas a pagar/receber de planilhas (exportação do ERP ou controle manual)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from finance_engine.core.exceptions import ParseError, RowParseError
from finance_engine.ingestion.normalization import normalize_text, parse_amount, parse_date
from finance_engine.ingestion.tabular import CSVParser, ExcelParser, normalize_header

SYN = {
    "counterparty": (
        "CLIENTE",
        "FORNECEDOR",
        "SACADO",
        "FAVORECIDO",
        "NOME",
        "RAZAO SOCIAL",
        "CEDENTE",
        "PAGADOR",
    ),
    "document": ("CNPJ", "CPF", "CNPJ/CPF", "CPF/CNPJ", "DOCUMENTO DO CLIENTE"),
    "number": (
        "DOCUMENTO",
        "TITULO",
        "NUMERO",
        "NF",
        "NOTA",
        "DUPLICATA",
        "N DOCUMENTO",
        "NUM DOC",
    ),
    "issue": ("EMISSAO", "DATA EMISSAO", "DT EMISSAO"),
    "due": ("VENCIMENTO", "DATA VENCIMENTO", "DT VENCIMENTO", "VENC"),
    "amount": ("VALOR", "VALOR ORIGINAL", "VALOR DO TITULO", "VLR"),
    "open": ("SALDO", "VALOR EM ABERTO", "SALDO EM ABERTO", "A RECEBER", "A PAGAR"),
    "paid": (
        "PAGAMENTO",
        "DATA PAGAMENTO",
        "RECEBIMENTO",
        "DATA RECEBIMENTO",
        "BAIXA",
        "DATA BAIXA",
    ),
    "kind": ("TIPO", "NATUREZA"),
}
_INDEX = {normalize_header(s): k for k, names in SYN.items() for s in names}


@dataclass(frozen=True, slots=True)
class RawTitle:
    receivable: bool
    counterparty: str
    counterparty_document: str | None
    document_number: str | None
    issue_date: date | None
    due_date: date
    amount: Decimal
    open_amount: Decimal
    paid_date: date | None
    line: int


def parse_titles(content: bytes, *, default_receivable: bool) -> tuple[list[RawTitle], list[str]]:
    grid = (ExcelParser() if content[:2] == b"PK" else CSVParser())._read_grid(content)
    header_idx, cols = -1, {}
    for i in range(min(30, len(grid))):
        found: dict[str, int] = {}
        for j, cell in enumerate(grid.iloc[i].tolist()):
            role = _INDEX.get(normalize_header(cell)) if cell is not None else None
            if role and role not in found:
                found[role] = j
        if "due" in found and ("amount" in found or "open" in found):
            header_idx, cols = i, found
            break
    if header_idx < 0:
        raise ParseError("cabeçalho não encontrado: preciso de Vencimento e Valor")
    out: list[RawTitle] = []
    errors: list[str] = []

    def blank(v: Any) -> bool:
        return v is None or str(v).strip() in ("", "nan", "NaT")

    for i, row in enumerate(
        grid.iloc[header_idx + 1 :].itertuples(index=False, name=None), start=header_idx + 2
    ):

        def cell(k: str, row: tuple[Any, ...] = row) -> Any:
            return row[cols[k]] if k in cols and cols[k] < len(row) else None

        if blank(cell("due")) and blank(cell("amount")):
            continue
        try:
            amount = abs(
                parse_amount(cell("amount") if not blank(cell("amount")) else cell("open"))
            )
            open_amount = abs(parse_amount(cell("open"))) if not blank(cell("open")) else amount
            paid = parse_date(cell("paid")) if not blank(cell("paid")) else None
            if paid and blank(cell("open")):
                open_amount = Decimal("0")
            kind_txt = normalize_text(str(cell("kind") or ""))
            receivable = (
                default_receivable if not kind_txt else not kind_txt.startswith(("P", "D", "S"))
            )
            doc: Any = cell("document")
            out.append(
                RawTitle(
                    receivable=receivable,
                    counterparty=str(cell("counterparty") or "Sem nome").strip(),
                    counterparty_document="".join(ch for ch in str(doc) if ch.isdigit()) or None
                    if not blank(doc)
                    else None,
                    document_number=str(cell("number")).strip()
                    if not blank(cell("number"))
                    else None,
                    issue_date=parse_date(cell("issue")) if not blank(cell("issue")) else None,
                    due_date=parse_date(cell("due")),
                    amount=amount,
                    open_amount=min(open_amount, amount),
                    paid_date=paid,
                    line=i,
                )
            )
        except RowParseError as exc:
            errors.append(f"Linha {i}: {exc}")
    if not out:
        raise ParseError("nenhum título válido; " + "; ".join(errors[:3]))
    return out, errors
