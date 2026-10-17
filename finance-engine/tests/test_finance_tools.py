"""Aging, conciliação, antecipação, plano de contas, indicadores, extração e IA."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from finance_engine.ai import AIProviderError, ClaudeProvider, GeminiProvider, OpenAIProvider
from finance_engine.analysis.aging import AgingItem, average_delay_days, build_aging
from finance_engine.analysis.anticipation import (
    AnticipationTerms,
    Receivable,
    simulate_anticipation,
)
from finance_engine.analysis.chart_of_accounts import StatementLine, complete, map_lines
from finance_engine.analysis.indicators import compute_indicators
from finance_engine.analysis.reconciliation import BankEntry, OpenTitle, suggest_matches
from finance_engine.core.exceptions import ParseError
from finance_engine.ingestion.statements import (
    EXTRACTION_SCHEMA,
    AIStatementExtractor,
    extract_statements,
    lines_from_text,
    parse_ecd,
)
from finance_engine.ingestion.titles import parse_titles
from finance_engine.models.enums import StatementKind

D = Decimal
REF = date(2024, 6, 30)


# ----------------------------------------------------------------------------- aging


def test_aging_buckets_overdue_and_concentration() -> None:
    items = [
        AgingItem("Alfa", REF - timedelta(days=100), D("1000")),
        AgingItem("Alfa", REF - timedelta(days=10), D("500")),
        AgingItem("Beta", REF + timedelta(days=5), D("2000")),
        AgingItem("Gama", REF + timedelta(days=120), D("1500")),
        AgingItem("Zero", REF, D("0")),  # quitado: ignorado
    ]
    r = build_aging(items, REF)
    assert r.total_open == D("5000")
    assert r.total_overdue == D("1500")
    assert r.overdue_ratio == D("0.3")
    by_label = {b.label: b for b in r.buckets}
    assert by_label["Vence em até 30 dias"].total == D("2000")
    assert by_label["Vence após 90 dias"].total == D("1500")
    assert sum(b.total for b in r.buckets) == r.total_open
    alfa = next(c for c in r.top_counterparties if c.counterparty == "Alfa")
    assert alfa.overdue == D("1500") and alfa.oldest_overdue_days == 100
    assert r.concentration_top5 == D("1")


def test_average_delay() -> None:
    pairs = [(date(2024, 1, 10), date(2024, 1, 15)), (date(2024, 2, 10), date(2024, 2, 9))]
    assert average_delay_days(pairs) == D("2")
    assert average_delay_days([]) == D("0")


# ----------------------------------------------------------------------------- conciliação


def _title(cp: str, due: date, amount: str, number: str | None = None) -> OpenTitle:
    return OpenTitle(uuid.uuid4(), True, cp, None, number, due, D(amount))


def test_reconciliation_one_to_one_and_group() -> None:
    t1 = _title("Mercado Bom Preço", date(2024, 3, 10), "1500.00", "NF 123")
    t2 = _title("Padaria Sol", date(2024, 3, 12), "300.00")
    t3 = _title("Padaria Sol", date(2024, 3, 15), "200.00")
    other = _title("Cliente Distante", date(2024, 8, 1), "999.00")
    e1 = BankEntry(uuid.uuid4(), date(2024, 3, 11), D("1500.00"), "PIX RECEBIDO MERCADO BOM PRECO")
    e2 = BankEntry(uuid.uuid4(), date(2024, 3, 15), D("500.00"), "TED PADARIA SOL LTDA")
    e3 = BankEntry(uuid.uuid4(), date(2024, 3, 15), D("-500.00"), "PAGAMENTO BOLETO")
    out = {s.transaction_id: s for s in suggest_matches([e1, e2, e3], [t1, t2, t3, other])}
    assert out[e1.id].title_ids == (t1.id,)
    assert out[e1.id].auto
    assert set(out[e2.id].title_ids) == {t2.id, t3.id}
    assert sum(out[e2.id].allocated) == D("500.00")
    assert e3.id not in out  # débito não paga título a receber


def test_reconciliation_rejects_far_amount() -> None:
    t = _title("Cliente X", date(2024, 3, 10), "1000.00")
    e = BankEntry(uuid.uuid4(), date(2024, 3, 10), D("700.00"), "PIX CLIENTE X")
    assert suggest_matches([e], [t]) == []


# ----------------------------------------------------------------------------- antecipação


def test_anticipation_discount_iof_and_cet() -> None:
    op = date(2024, 1, 1)
    terms = AnticipationTerms(
        monthly_rate=D("0.02"), operation_date=op, float_days=0, fee_per_title=D("5")
    )
    r = simulate_anticipation([Receivable(D("10000"), op + timedelta(days=30), "NF1")], terms)
    t = r.titles[0]
    assert t.days == 30 and t.discount == D("200.00")
    # IOF PJ: 0,0041% a.d. x 30 + 0,38% sobre o valor líquido do deságio
    assert t.iof == (D("9800") * (D("0.000041") * 30 + D("0.0038"))).quantize(D("0.01"))
    assert r.net_value == D("10000") - D("200.00") - t.iof - D("5.00")
    assert r.effective_monthly_rate > D("0.02")  # IOF e tarifa encarecem
    assert r.effective_annual_rate > r.effective_monthly_rate * 12


def test_anticipation_recommends_cheaper_alternative_and_skips_overdue() -> None:
    op = date(2024, 1, 1)
    terms = AnticipationTerms(
        monthly_rate=D("0.05"), operation_date=op, alternative_monthly_rate=D("0.015")
    )
    r = simulate_anticipation(
        [
            Receivable(D("5000"), op + timedelta(days=60)),
            Receivable(D("100"), op - timedelta(days=3), "vencido"),
        ],
        terms,
    )
    assert len(r.titles) == 1
    assert any("vencido" in w for w in r.warnings)
    assert r.alternative_cost is not None and r.alternative_cost < r.total_cost
    assert "linha alternativa" in r.recommendation
    with pytest.raises(ValueError):
        simulate_anticipation([], terms)


# ----------------------------------------------------------------------------- plano e indicadores

BALANCE = [
    ("ATIVO", "1000000"),
    ("ATIVO CIRCULANTE", "600000"),
    ("Caixa e equivalentes de caixa", "100000"),
    ("Clientes", "300000"),
    ("Estoques", "200000"),
    ("ATIVO NÃO CIRCULANTE", "400000"),
    ("PASSIVO CIRCULANTE", "350000"),
    ("Fornecedores", "150000"),
    ("Empréstimos e financiamentos", "120000"),
    ("Salários e encargos", "50000"),
    ("Obrigações tributárias", "30000"),
    ("PASSIVO NÃO CIRCULANTE", "150000"),
    ("Empréstimos e financiamentos", "150000"),
    ("PATRIMÔNIO LÍQUIDO", "500000"),
]
DRE = [
    ("Receita operacional bruta", "2400000"),
    ("Deduções da receita", "240000"),
    ("Receita operacional líquida", "2160000"),
    ("Custo das mercadorias vendidas", "1440000"),
    ("Lucro bruto", "720000"),
    ("Despesas com vendas", "200000"),
    ("Despesas administrativas", "300000"),
    ("Despesas financeiras", "60000"),
    ("Lucro líquido do exercício", "110000"),
]


def _mapped() -> dict[str, Decimal]:
    m = map_lines([StatementLine(d, D(v)) for d, v in BALANCE + DRE])
    complete(m)
    assert not m.warnings, m.warnings
    return m.values


def test_chart_mapping_is_section_aware() -> None:
    v = _mapped()
    assert v["PC_EMPRESTIMOS"] == D("120000")
    assert v["PNC_EMPRESTIMOS"] == D("150000")
    assert v["AT_TOTAL"] == D("1000000")
    assert v["CMV"] == D("1440000")


def test_complete_flags_unbalanced_sheet() -> None:
    m = map_lines(
        [
            StatementLine("Ativo total", D("100")),
            StatementLine("Passivo circulante", D("30")),
            StatementLine("Patrimônio líquido", D("50")),
        ]
    )
    complete(m)
    assert any("revise o mapeamento" in w for w in m.warnings)


def test_indicators_fleuriet_break_even_and_alerts() -> None:
    i = compute_indicators(_mapped())
    assert i.pmr == D("45.0")  # 300k / 2,4M x 360
    assert i.pmp == D("37.5")  # 150k / 1,44M x 360
    assert i.pme == D("50.0")
    assert i.cash_cycle == D("57.5")
    assert i.ncg == D("270000.00")  # (300k + 200k) − (150k + 50k + 30k)
    assert i.working_capital == D("250000.00")  # 500k + 150k − 400k
    assert i.treasury_balance == D("-20000.00")
    assert i.current_ratio == D("1.71")
    assert i.break_even_revenue is not None and i.break_even_revenue < D("2160000")
    assert any("tesouraria negativo" in a for a in i.alerts)
    assert compute_indicators({}).missing


# ----------------------------------------------------------------------------- extração

ECD = (
    "|0000|LECD|01012023|31122023|EMPRESA TESTE LTDA|11222333000181|\n"
    "|J100|1|T|1||A|ATIVO|900000,00|D|1000000,00|D||\n"
    "|J100|1.1|T|2|1|A|ATIVO CIRCULANTE|500000,00|D|600000,00|D||\n"
    "|J100|3|T|1||P|PATRIMONIO LIQUIDO|400000,00|C|500000,00|C||\n"
    "|J150|1|3.01|T|1||RECEITA BRUTA|0,00|C|2400000,00|C|R||\n"
    "|J150|2|3.99|T|1||LUCRO LIQUIDO DO EXERCICIO|0,00|C|110000,00|C|R||\n"
)


def test_parse_ecd_uses_current_period() -> None:
    bal, dre = parse_ecd(ECD.encode("latin-1"))
    assert bal.kind is StatementKind.BALANCE_SHEET and dre.kind is StatementKind.INCOME_STATEMENT
    assert bal.period_end == date(2023, 12, 31)
    assert bal.lines[0].description == "ATIVO" and bal.lines[0].value == D("1000000.00")
    assert dre.lines[-1].value == D("110000.00")


def test_lines_from_pdf_text_and_table_upload() -> None:
    text = "Clientes ........ 300.000,00  250.000,00\nRodapé sem valor\nEstoques   (1.200,50)"
    lines = lines_from_text(text)
    assert [(ln.description, ln.value) for ln in lines] == [
        ("Clientes", D("300000.00")),
        ("Estoques", D("-1200.50")),
    ]
    csv = "\n".join(f"{d};{v}" for d, v in DRE).encode()
    (st,) = extract_statements(csv, "dre.csv")
    assert st.kind is StatementKind.INCOME_STATEMENT and len(st.lines) == len(DRE)
    with pytest.raises(ParseError):
        extract_statements(b"%PDF-1.4 sem texto", "scan.pdf")


class _FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bytes | None]] = []

    def extract_json(
        self, prompt: str, schema: dict[str, Any], *, pdf: bytes | None = None
    ) -> dict[str, Any]:
        self.calls.append((prompt, schema, pdf))
        return {
            "kind": "INCOME_STATEMENT",
            "company": "Empresa",
            "period_end": "2023-12-31",
            "lines": [
                {"description": "Receita bruta", "value": 2400000, "code": "RECEITA_BRUTA"},
                {"description": "Outras", "value": 10, "code": "OTHER"},
            ],
        }


def test_ai_extraction_is_provider_agnostic() -> None:
    fake = _FakeProvider()
    (st,) = extract_statements(
        b"%PDF-1.4 x", "dre.pdf", llm=AIStatementExtractor(fake), use_ai=True
    )
    assert fake.calls[0][1] is EXTRACTION_SCHEMA and fake.calls[0][2] == b"%PDF-1.4 x"
    assert st.preset_codes == {"RECEITA_BRUTA": D("2400000")}
    assert st.period_end == date(2023, 12, 31)


# ----------------------------------------------------------------------------- conectores de IA

ANSWER = {"ok": True}


class _Recorder:
    def __init__(self, result: Any) -> None:
        self.result, self.kwargs = result, {}

    def __call__(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.result


def test_claude_provider_sends_pdf_and_schema() -> None:
    create = _Recorder(
        SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(ANSWER))],
        )
    )
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    p = ClaudeProvider(client=client)
    assert p.extract_json("leia", {"type": "object"}, pdf=b"%PDF") == ANSWER
    kw = create.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    doc = kw["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    create.result.stop_reason = "refusal"
    with pytest.raises(AIProviderError):
        p.extract_json("leia", {})


def test_openai_provider_sends_file_and_strict_schema() -> None:
    message = SimpleNamespace(content=json.dumps(ANSWER), refusal=None)
    create = _Recorder(
        SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p = OpenAIProvider(client=client, model="modelo-x")
    assert p.extract_json("leia", {"type": "object"}, pdf=b"%PDF") == ANSWER
    part = create.kwargs["messages"][0]["content"][0]
    assert part["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert create.kwargs["response_format"]["json_schema"]["strict"] is True
    with pytest.raises(AIProviderError):
        OpenAIProvider(client=client)  # modelo obrigatório


def test_gemini_provider_requests_json() -> None:
    pytest.importorskip("google.genai")
    gen = _Recorder(SimpleNamespace(text=json.dumps(ANSWER)))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=gen))
    p = GeminiProvider(client=client, model="modelo-g")
    assert p.extract_json("leia", {"type": "object"}, pdf=b"%PDF") == ANSWER
    assert gen.kwargs["config"].response_mime_type == "application/json"
    gen.result = SimpleNamespace(text="não é json")
    with pytest.raises(AIProviderError):
        p.extract_json("leia", {})


# ----------------------------------------------------------------------------- planilha de títulos


def test_parse_titles_sheet() -> None:
    csv = (
        "Relatório de contas a receber;;;;;\n"
        "Cliente;CNPJ;Documento;Emissão;Vencimento;Valor;Saldo;Pagamento\n"
        "Mercado Bom Preço;11.222.333/0001-81;NF 10;01/03/2024;10/03/2024;1.500,00;1.500,00;\n"
        "Padaria Sol;;NF 11;01/03/2024;12/03/2024;300,00;;15/03/2024\n"
        ";;;;;;;\n"
        "Erro;;NF 12;;data ruim;100,00;;\n"
    ).encode()
    titles, errors = parse_titles(csv, default_receivable=True)
    assert len(titles) == 2 and len(errors) == 1
    assert titles[0].counterparty_document == "11222333000181"
    assert titles[1].open_amount == D("0") and titles[1].paid_date == date(2024, 3, 15)
