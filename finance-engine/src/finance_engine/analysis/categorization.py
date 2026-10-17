"""Motor de categorização por palavras-chave.

Regras são avaliadas contra o histórico **normalizado** (maiúsculo, sem acentos), por ordem de:
1. prioridade (maior primeiro);
2. especificidade — regra do cliente antes da regra da organização;
3. tamanho do padrão (padrão mais longo = mais específico: "TARIFA PIX" antes de "PIX").

``CONTAINS`` casa por palavra inteira: "REDE" não casa com "PAREDE", "TIM" não casa com "TIMBRE".
O motor é puro (sem banco de dados) para ser testável e reutilizável em jobs de reprocessamento.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from finance_engine.ingestion.normalization import normalize_text
from finance_engine.models.enums import CategoryGroup, MatchType, TransactionType


@dataclass(frozen=True, slots=True)
class RuleSpec:
    id: uuid.UUID
    pattern: str
    category_id: uuid.UUID
    match_type: MatchType = MatchType.CONTAINS
    applies_to: TransactionType | None = None
    cost_center_id: uuid.UUID | None = None
    priority: int = 100
    client_specific: bool = False


@dataclass(frozen=True, slots=True)
class CategorizationMatch:
    rule_id: uuid.UUID
    category_id: uuid.UUID
    cost_center_id: uuid.UUID | None


class Categorizable(Protocol):
    @property
    def normalized_description(self) -> str: ...
    @property
    def type(self) -> TransactionType: ...


@dataclass(frozen=True, slots=True)
class _CompiledRule:
    spec: RuleSpec
    regex: re.Pattern[str]


def _compile(spec: RuleSpec) -> _CompiledRule:
    if spec.match_type is MatchType.REGEX:
        try:
            regex = re.compile(spec.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"regex inválida na regra {spec.id}: {exc}") from exc
    else:
        escaped = r"\s+".join(re.escape(part) for part in normalize_text(spec.pattern).split())
        prefix = r"^" if spec.match_type is MatchType.STARTS_WITH else r"(?<![A-Z0-9])"
        regex = re.compile(prefix + escaped + r"(?![A-Z0-9])")
    return _CompiledRule(spec, regex)


class CategorizationEngine:
    def __init__(self, rules: Iterable[RuleSpec]) -> None:
        ordered = sorted(rules, key=lambda r: (-r.priority, not r.client_specific, -len(r.pattern)))
        self._rules: list[_CompiledRule] = [_compile(r) for r in ordered]

    def __len__(self) -> int:
        return len(self._rules)

    def match(
        self, normalized_description: str, tx_type: TransactionType
    ) -> CategorizationMatch | None:
        for rule in self._rules:
            if rule.spec.applies_to is not None and rule.spec.applies_to is not tx_type:
                continue
            if rule.regex.search(normalized_description):
                return CategorizationMatch(
                    rule.spec.id, rule.spec.category_id, rule.spec.cost_center_id
                )
        return None

    def match_many(self, items: Sequence[Categorizable]) -> list[CategorizationMatch | None]:
        return [self.match(i.normalized_description, i.type) for i in items]


# --------------------------------------------------------------------------- taxonomia padrão


@dataclass(frozen=True, slots=True)
class DefaultCategory:
    name: str
    group: CategoryGroup
    patterns: tuple[str, ...]
    applies_to: TransactionType | None = None
    priority: int = 100


IN, OUT = TransactionType.INCOME, TransactionType.EXPENSE

# fmt: off
DEFAULT_TAXONOMY: tuple[DefaultCategory, ...] = (
    DefaultCategory(
        "Receita de Vendas - Cartões", CategoryGroup.REVENUE,
        ("CIELO", "REDE", "REDECARD", "STONE", "GETNET", "PAGSEGURO", "SUMUP", "MERCADO PAGO",
         "PAGAR ME", "SAFRAPAY", "VERO"),
        applies_to=IN,
    ),
    DefaultCategory(
        "Receita de Vendas - Boletos", CategoryGroup.REVENUE,
        ("LIQUIDACAO COBRANCA", "LIQ COBRANCA", "CRED COBRANCA", "COBRANCA LIQUIDADA"),
        applies_to=IN,
    ),
    DefaultCategory(
        "Recebimentos PIX/TED", CategoryGroup.REVENUE,
        ("PIX RECEBIDO", "PIX RECEB", "TED RECEBIDA", "DOC RECEBIDO", "TRANSF RECEBIDA",
         "CREDITO PIX"),
        applies_to=IN, priority=90,
    ),
    DefaultCategory(
        "Custo de Mercadorias", CategoryGroup.COGS,
        ("FORNECEDOR", "FORNECEDORES", "COMPRA DE MERCADORIA", "MATERIA PRIMA", "TECIDOS"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Energia Elétrica", CategoryGroup.OPERATING_EXPENSE,
        ("ENERGIA", "CEMIG", "ENEL", "COPEL", "LIGHT", "CELESC", "CPFL", "EQUATORIAL",
         "NEOENERGIA", "COELBA", "ELEKTRO", "RGE", "ENERGISA"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Água e Saneamento", CategoryGroup.OPERATING_EXPENSE,
        ("SABESP", "COPASA", "CEDAE", "SANEPAR", "CORSAN", "EMBASA", "DMAE", "AGUA E ESGOTO"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Telefonia e Internet", CategoryGroup.OPERATING_EXPENSE,
        ("VIVO", "CLARO", "TIM", "OI", "TELEFONICA", "NET SERVICOS", "INTERNET", "ALGAR"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Aluguel e Condomínio", CategoryGroup.OPERATING_EXPENSE,
        ("ALUGUEL", "CONDOMINIO", "LOCACAO IMOVEL"), applies_to=OUT,
    ),
    DefaultCategory(
        "Folha de Pagamento", CategoryGroup.PAYROLL,
        ("SALARIO", "SALARIOS", "FOLHA", "FOLHA PAGAMENTO", "PRO LABORE", "FERIAS", "RESCISAO",
         "13 SALARIO", "VALE TRANSPORTE", "VALE REFEICAO"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Encargos Trabalhistas", CategoryGroup.PAYROLL, ("FGTS", "INSS", "GPS", "ESOCIAL"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Impostos e Tributos", CategoryGroup.TAXES,
        ("DARF", "DAS SIMPLES", "PGTO DAS", "DAS MEI", "SIMPLES NACIONAL", "ICMS", "ISS", "ISSQN",
         "IPTU", "IPVA", "GARE",
         "DARE", "PIS", "COFINS", "IRPJ", "CSLL", "TRIBUTO", "RECEITA FEDERAL"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Tarifas Bancárias", CategoryGroup.FINANCIAL,
        ("TARIFA", "TAR", "TARIFAS", "CESTA", "PACOTE SERVICOS", "MANUTENCAO CONTA",
         "TAXA", "ANUIDADE"),
        applies_to=OUT, priority=110,
    ),
    DefaultCategory(
        "Juros e IOF", CategoryGroup.FINANCIAL,
        ("JUROS", "IOF", "ENCARGOS", "MORA", "CHEQUE ESPECIAL", "LIMITE"), applies_to=OUT,
        priority=110,
    ),
    DefaultCategory(
        "Rendimentos Financeiros", CategoryGroup.FINANCIAL,
        ("RENDIMENTO", "RENDIMENTOS", "REND PAGO", "JUROS CREDITADOS"), applies_to=IN,
    ),
    DefaultCategory(
        "Aplicações e Resgates", CategoryGroup.TRANSFER,
        ("APLICACAO", "RESGATE", "CDB", "POUPANCA", "INVEST FACIL", "APLIC AUT", "RESG AUT"),
        priority=120,
    ),
    DefaultCategory(
        "Transferências entre Contas", CategoryGroup.TRANSFER,
        ("TRANSF ENTRE CONTAS", "TRANSFERENCIA ENTRE CONTAS", "MESMA TITULARIDADE"),
        priority=120,
    ),
    DefaultCategory(
        "Combustível e Transporte", CategoryGroup.OPERATING_EXPENSE,
        ("POSTO", "COMBUSTIVEL", "SHELL", "IPIRANGA", "PETROBRAS", "UBER", "99 TAXI",
         "99APP", "ESTACIONAMENTO", "PEDAGIO", "SEM PARAR", "CONECTCAR"),
        applies_to=OUT,
    ),
    DefaultCategory(
        "Alimentação", CategoryGroup.PERSONAL,
        ("IFOOD", "RESTAURANTE", "LANCHONETE", "PADARIA", "SUPERMERCADO", "MERCADINHO"),
        applies_to=OUT, priority=80,
    ),
    DefaultCategory(
        "Software e Assinaturas", CategoryGroup.OPERATING_EXPENSE,
        ("GOOGLE", "MICROSOFT", "AWS", "AMAZON WEB", "ADOBE", "NETFLIX", "SPOTIFY", "APPLE COM"),
        applies_to=OUT,
    ),
)
# fmt: on
