"""Plano de contas gerencial padronizado e o mapeamento de linhas de demonstrativos para ele.

Balanços e DREs chegam com nomes diferentes em cada contabilidade ("Duplicatas a receber",
"Clientes", "Contas a receber"). O mapeador lê o demonstrativo em ordem, acompanha a seção em que
está (ativo circulante, passivo circulante...) e associa cada linha a um código padrão. A primeira
linha que casa com um código vence — em demonstrativos, o agrupador vem antes das subcontas, o
que evita somar o mesmo valor duas vezes. Tudo que não casa fica listado para revisão humana.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from finance_engine.ingestion.normalization import normalize_text

STANDARD_ACCOUNTS: dict[str, str] = {
    # Balanço
    "AC_CAIXA": "Caixa, bancos e aplicações de liquidez imediata",
    "AC_CLIENTES": "Clientes / contas a receber",
    "AC_ESTOQUES": "Estoques",
    "AC_OUTROS": "Outros ativos circulantes",
    "AC_TOTAL": "Ativo circulante",
    "ANC_TOTAL": "Ativo não circulante",
    "AT_TOTAL": "Ativo total",
    "PC_FORNECEDORES": "Fornecedores",
    "PC_EMPRESTIMOS": "Empréstimos e financiamentos (curto prazo)",
    "PC_SALARIOS": "Obrigações trabalhistas",
    "PC_IMPOSTOS": "Obrigações tributárias",
    "PC_OUTROS": "Outros passivos circulantes",
    "PC_TOTAL": "Passivo circulante",
    "PNC_EMPRESTIMOS": "Empréstimos e financiamentos (longo prazo)",
    "PNC_TOTAL": "Passivo não circulante",
    "PL_TOTAL": "Patrimônio líquido",
    # DRE (valores de despesas/custos em módulo)
    "RECEITA_BRUTA": "Receita bruta",
    "DEDUCOES": "Deduções da receita (impostos, devoluções)",
    "RECEITA_LIQUIDA": "Receita líquida",
    "CMV": "Custo das mercadorias, produtos ou serviços",
    "LUCRO_BRUTO": "Lucro bruto",
    "DESPESAS_VENDAS": "Despesas com vendas",
    "DESPESAS_ADM": "Despesas administrativas e gerais",
    "DESPESAS_FINANCEIRAS": "Despesas financeiras",
    "RECEITAS_FINANCEIRAS": "Receitas financeiras",
    "DEPRECIACAO": "Depreciação e amortização",
    "LUCRO_LIQUIDO": "Lucro (prejuízo) líquido",
}

_SECTION_RULES: list[tuple[str, str]] = [
    (r"^ATIVO (NAO|NÃO) CIRCULANTE|^REALIZAVEL A LONGO PRAZO|^ATIVO PERMANENTE", "ANC"),
    (r"^ATIVO CIRCULANTE", "AC"),
    (r"^PASSIVO (NAO|NÃO) CIRCULANTE|^EXIGIVEL A LONGO PRAZO", "PNC"),
    (r"^PASSIVO CIRCULANTE", "PC"),
    (r"^PATRIMONIO LIQUIDO", "PL"),
]

# (código, regex, seções onde vale; None = qualquer)
_RULES: list[tuple[str, str, tuple[str, ...] | None]] = [
    ("AT_TOTAL", r"^(TOTAL DO ATIVO|ATIVO TOTAL|TOTAL ATIVO|ATIVO)$", None),
    ("AC_TOTAL", r"^(TOTAL DO )?ATIVO CIRCULANTE$", None),
    ("ANC_TOTAL", r"^(TOTAL DO )?ATIVO (NAO|NÃO) CIRCULANTE$", None),
    ("PC_TOTAL", r"^(TOTAL DO )?PASSIVO CIRCULANTE$", None),
    ("PNC_TOTAL", r"^(TOTAL DO )?(PASSIVO (NAO|NÃO) CIRCULANTE|EXIGIVEL A LONGO PRAZO)$", None),
    ("PL_TOTAL", r"^(TOTAL DO )?PATRIMONIO LIQUIDO$", None),
    (
        "AC_CAIXA",
        r"^(CAIXA|DISPONIBILIDADES|DISPONIVEL|BANCOS|CAIXA E EQUIVALENTES|APLICACOES FINANCEIRAS)",
        ("AC",),
    ),
    (
        "AC_CLIENTES",
        r"^(CLIENTES|DUPLICATAS A RECEBER|CONTAS A RECEBER|CREDITOS? (COM|DE) CLIENTES|RECEBIVEIS)",
        ("AC",),
    ),
    ("AC_ESTOQUES", r"^ESTOQUES?", ("AC",)),
    ("PC_FORNECEDORES", r"^(FORNECEDORES|CONTAS A PAGAR|DUPLICATAS A PAGAR)", ("PC",)),
    ("PC_EMPRESTIMOS", r"^(EMPRESTIMOS|FINANCIAMENTOS|EMPRESTIMOS E FINANCIAMENTOS)", ("PC",)),
    ("PNC_EMPRESTIMOS", r"^(EMPRESTIMOS|FINANCIAMENTOS|EMPRESTIMOS E FINANCIAMENTOS)", ("PNC",)),
    (
        "PC_SALARIOS",
        r"^(OBRIGACOES TRABALHISTAS|SALARIOS|ENCARGOS SOCIAIS|OBRIGACOES SOCIAIS)",
        ("PC",),
    ),
    ("PC_IMPOSTOS", r"^(OBRIGACOES (FISCAIS|TRIBUTARIAS)|IMPOSTOS|TRIBUTOS)", ("PC",)),
    (
        "RECEITA_BRUTA",
        r"^(RECEITA (OPERACIONAL )?BRUTA|FATURAMENTO BRUTO|RECEITA BRUTA DE VENDAS)",
        None,
    ),
    ("DEDUCOES", r"^(\(-\) )?(DEDUCOES|IMPOSTOS SOBRE (VENDAS|A RECEITA)|DEVOLUCOES)", None),
    ("RECEITA_LIQUIDA", r"^RECEITA (OPERACIONAL )?LIQUIDA", None),
    ("CMV", r"^(\(-\) )?(CUSTO|CMV|CPV|CSP)", None),
    ("LUCRO_BRUTO", r"^(LUCRO|RESULTADO) BRUTO", None),
    ("DESPESAS_VENDAS", r"^(\(-\) )?DESPESAS (COM|DE) VENDAS|^DESPESAS COMERCIAIS", None),
    ("DESPESAS_ADM", r"^(\(-\) )?DESPESAS (ADMINISTRATIVAS|GERAIS)", None),
    ("DESPESAS_FINANCEIRAS", r"^(\(-\) )?DESPESAS FINANCEIRAS", None),
    ("RECEITAS_FINANCEIRAS", r"^RECEITAS FINANCEIRAS", None),
    ("DEPRECIACAO", r"^(\(-\) )?DEPRECIACAO", None),
    ("LUCRO_LIQUIDO", r"^(LUCRO|PREJUIZO|RESULTADO) (LIQUIDO|DO EXERCICIO|DO PERIODO)", None),
]
_COMPILED = [(c, re.compile(r), s) for c, r, s in _RULES]
_SECTIONS = [(re.compile(r), s) for r, s in _SECTION_RULES]


@dataclass(frozen=True, slots=True)
class StatementLine:
    description: str
    value: Decimal  # em módulo ou com sinal; o mapeador guarda o módulo
    level: int | None = None


@dataclass(slots=True)
class MappedStatement:
    values: dict[str, Decimal] = field(default_factory=dict)
    matched: list[tuple[str, str]] = field(default_factory=list)  # (descrição, código)
    unmatched: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean(desc: str) -> str:
    t = normalize_text(desc)
    t = re.sub(r"^[\d\.\s]+(?=[A-Z(])", "", t)  # remove numeração "1.1.01 "
    return re.sub(r"\s*\(?R\$\)?\s*$", "", t).strip(" :-")


def map_lines(lines: list[StatementLine]) -> MappedStatement:
    out = MappedStatement()
    section: str | None = None
    for line in lines:
        desc = _clean(line.description)
        if not desc:
            continue
        for rx, sec in _SECTIONS:
            if rx.search(desc):
                section = sec
                break
        code = next(
            (
                c
                for c, rx, secs in _COMPILED
                if rx.search(desc) and (secs is None or section in secs)
            ),
            None,
        )
        if code is None:
            out.unmatched.append(line.description)
            continue
        if code in out.values:
            continue  # agrupador já capturado; subconta não soma de novo
        out.values[code] = abs(line.value)
        out.matched.append((line.description, code))
    complete(out)
    return out


def complete(m: MappedStatement) -> None:
    """Deriva totais ausentes e confere a equação patrimonial."""
    v = m.values
    if "RECEITA_LIQUIDA" not in v and "RECEITA_BRUTA" in v:
        v["RECEITA_LIQUIDA"] = v["RECEITA_BRUTA"] - v.get("DEDUCOES", Decimal("0"))
    if "LUCRO_BRUTO" not in v and "RECEITA_LIQUIDA" in v and "CMV" in v:
        v["LUCRO_BRUTO"] = v["RECEITA_LIQUIDA"] - v["CMV"]
    if "AC_OUTROS" not in v and "AC_TOTAL" in v:
        rest = v["AC_TOTAL"] - sum(
            (v.get(k, Decimal("0")) for k in ("AC_CAIXA", "AC_CLIENTES", "AC_ESTOQUES")),
            Decimal("0"),
        )
        if rest >= 0:
            v["AC_OUTROS"] = rest
    if "PC_OUTROS" not in v and "PC_TOTAL" in v:
        rest = v["PC_TOTAL"] - sum(
            (
                v.get(k, Decimal("0"))
                for k in ("PC_FORNECEDORES", "PC_EMPRESTIMOS", "PC_SALARIOS", "PC_IMPOSTOS")
            ),
            Decimal("0"),
        )
        if rest >= 0:
            v["PC_OUTROS"] = rest
    if all(k in v for k in ("AT_TOTAL", "PC_TOTAL", "PL_TOTAL")):
        passivo = v["PC_TOTAL"] + v.get("PNC_TOTAL", Decimal("0")) + v["PL_TOTAL"]
        if abs(passivo - v["AT_TOTAL"]) > max(Decimal("1"), v["AT_TOTAL"] * Decimal("0.005")):
            m.warnings.append(
                f"Ativo total ({v['AT_TOTAL']}) diferente de passivo + PL ({passivo}): "
                "revise o mapeamento."
            )
