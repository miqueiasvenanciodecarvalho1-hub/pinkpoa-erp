from __future__ import annotations

import itertools
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests import samples

API = "/api/v1"
CNPJ = "11.222.333/0001-81"
CPF = "529.982.247-25"


def _setup(client: TestClient, slug: str = "consultoria-a") -> tuple[dict[str, str], str, str]:
    org = client.post(f"{API}/organizations", json={"name": "Consultoria A", "slug": slug})
    assert org.status_code == 201, org.text
    headers = {"X-Organization-Id": org.json()["id"]}
    cli = client.post(
        f"{API}/clients",
        json={"person_type": "PJ", "name": "Loja Exemplo", "document": CNPJ},
        headers=headers,
    )
    assert cli.status_code == 201, cli.text
    client_id = cli.json()["id"]
    acc = client.post(
        f"{API}/clients/{client_id}/bank-accounts",
        json={"bank_code": "341", "branch": "1234", "account_number": "56789-0"},
        headers=headers,
    )
    assert acc.status_code == 201, acc.text
    return headers, client_id, acc.json()["id"]


def _upload(
    client: TestClient,
    headers: dict[str, str],
    client_id: str,
    account_id: str,
    filename: str,
    content: bytes,
    **form: Any,
) -> Any:
    return client.post(
        f"{API}/clients/{client_id}/bank-accounts/{account_id}/imports",
        files={"file": (filename, content)},
        data=form,
        headers=headers,
    )


def test_client_document_validation(client: TestClient) -> None:
    headers, _, _ = _setup(client)
    bad = client.post(
        f"{API}/clients",
        json={"person_type": "PF", "name": "Fulano", "document": "111.111.111-11"},
        headers=headers,
    )
    assert bad.status_code == 422
    ok = client.post(
        f"{API}/clients",
        json={"person_type": "PF", "name": "Fulano", "document": CPF},
        headers=headers,
    )
    assert ok.status_code == 201
    assert ok.json()["document"] == "52998224725"


def test_ofx_import_sync_categorizes_and_rejects_duplicate_file(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    resp = _upload(client, headers, client_id, account_id, "jan.ofx", samples.ofx_bytes())
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["status"] == "COMPLETED"
    assert batch["source_format"] == "OFX"
    assert batch["imported_count"] == 6
    assert batch["categorized_count"] >= 4  # PIX, fornecedor, energia, tarifa, aluguel

    again = _upload(client, headers, client_id, account_id, "jan-copia.ofx", samples.ofx_bytes())
    assert again.status_code == 409
    assert again.json()["existing_batch_id"] == batch["id"]

    # saldo inicial derivado do LEDGERBAL (saldo final - soma do mês)
    accounts = client.get(f"{API}/clients/{client_id}/bank-accounts", headers=headers).json()
    # 10.209,35 - (15.000 - 4.500 - 890,15 - 0,50 - 3.200 - 1.200) = 5.000,00
    assert Decimal(accounts[0]["opening_balance"]) == Decimal("5000.00")
    assert accounts[0]["opening_balance_date"] == "2024-01-02"

    txs = client.get(
        f"{API}/clients/{client_id}/transactions", params={"uncategorized": True}, headers=headers
    ).json()
    assert txs["total"] == 1  # "Pagamento Ação Educação" não casa com nenhuma regra


def test_overlapping_statements_are_deduplicated(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    first = _upload(client, headers, client_id, account_id, "fev.csv", samples.csv_bytes()).json()
    assert first["imported_count"] == 4  # as duas tarifas idênticas são mantidas
    assert first["warnings"]  # linha quebrada reportada

    extended = samples.CSV_STATEMENT + "12/02/2024;PIX RECEBIDO CLIENTE DELTA;;800,00;\n"
    second = _upload(
        client, headers, client_id, account_id, "fev-completo.csv", extended.encode("cp1252")
    ).json()
    assert second["imported_count"] == 1
    assert second["duplicate_count"] == 4


def test_xlsx_with_column_mapping_in_background(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    mapping = {
        "date": "Dt. Movimento",
        "description": "Descrição do lançamento",
        "amount": "Montante",
        "dc_indicator": "D/C",
    }
    resp = _upload(
        client,
        headers,
        client_id,
        account_id,
        "marco.xlsx",
        samples.xlsx_bytes(),
        mode="background",
        sheet_name="Extrato",
        column_mapping=json.dumps(mapping),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "PENDING"
    status = client.get(f"{API}/imports/{resp.json()['id']}", headers=headers).json()
    assert status["status"] == "COMPLETED"
    assert status["imported_count"] == 3
    assert status["categorized_count"] == 3  # DARF, CIELO, FOLHA


def test_invalid_file_marks_batch_failed(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    content = b"Coluna1;Coluna2\nfoo;bar\n"
    resp = _upload(client, headers, client_id, account_id, "lixo.csv", content)
    assert resp.status_code == 422
    batch_id = resp.json()["batch_id"]
    status = client.get(f"{API}/imports/{batch_id}", headers=headers).json()
    assert status["status"] == "FAILED"
    assert "cabeçalho" in status["error_message"]

    bad_mapping = _upload(
        client, headers, client_id, account_id, "x.csv", b"a;b\n", column_mapping="{nope"
    )
    assert bad_mapping.status_code == 422


def test_cnab_retorno_import(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    resp = _upload(client, headers, client_id, account_id, "COB.RET", samples.cnab240_retorno())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_format"] == "CNAB240"
    assert body["imported_count"] == 1
    assert body["categorized_count"] == 1  # "LIQUIDACAO COBRANCA" -> Receita de Boletos


def test_tenant_isolation(client: TestClient) -> None:
    headers_a, client_a, account_a = _setup(client, "consultoria-a")
    headers_b, _, _ = _setup(client, "consultoria-b")
    assert (
        client.get(f"{API}/clients/{client_a}/transactions", headers=headers_b).status_code == 404
    )
    resp = _upload(client, headers_b, client_a, account_a, "jan.ofx", samples.ofx_bytes())
    assert resp.status_code == 404
    batch = _upload(client, headers_a, client_a, account_a, "jan.ofx", samples.ofx_bytes()).json()
    assert client.get(f"{API}/imports/{batch['id']}", headers=headers_b).status_code == 404


def test_custom_rule_recategorizes_and_manual_override(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    _upload(client, headers, client_id, account_id, "jan.ofx", samples.ofx_bytes())
    categories = {
        c["name"]: c["id"] for c in client.get(f"{API}/categories", headers=headers).json()
    }

    rule = client.post(
        f"{API}/categorization-rules",
        json={"pattern": "Ação Educação", "category_id": categories["Folha de Pagamento"]},
        headers=headers,
    )
    assert rule.status_code == 201, rule.text
    pending = client.get(
        f"{API}/clients/{client_id}/transactions", params={"uncategorized": True}, headers=headers
    ).json()
    assert pending["total"] == 0  # regra aplicada imediatamente (acentos ignorados)

    tx = client.get(
        f"{API}/clients/{client_id}/transactions", params={"type": "EXPENSE"}, headers=headers
    ).json()["items"][0]
    manual = client.patch(
        f"{API}/transactions/{tx['id']}/category",
        json={"category_id": categories["Custo de Mercadorias"]},
        headers=headers,
    )
    assert manual.json()["categorization_source"] == "MANUAL"
    redo = client.post(
        f"{API}/clients/{client_id}/recategorize", params={"overwrite": True}, headers=headers
    )
    assert redo.status_code == 200
    after = client.get(
        f"{API}/clients/{client_id}/transactions", params={"type": "EXPENSE"}, headers=headers
    ).json()["items"]
    kept = next(t for t in after if t["id"] == tx["id"])
    assert kept["category_id"] == categories["Custo de Mercadorias"]


def _monthly_history_csv(today: date) -> bytes:
    lines = ["Data;Histórico;Valor"]
    for back in range(5, 0, -1):
        month_start = (today.replace(day=1) - timedelta(days=30 * back)).replace(day=1)
        lines.append(f"{month_start.replace(day=5):%d/%m/%Y};RECEBIMENTO CIELO;4.000,00")
        lines.append(f"{month_start.replace(day=10):%d/%m/%Y};ALUGUEL LOJA;-3.000,00")
        lines.append(f"{month_start.replace(day=15):%d/%m/%Y};FOLHA PAGAMENTO;-2.500,00")
    return "\n".join(lines).encode()


def test_cash_flow_report_with_forecast_and_statement(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    today = date(2024, 6, 20)
    client_account = client.get(f"{API}/clients/{client_id}/bank-accounts", headers=headers)
    assert client_account.status_code == 200
    resp = _upload(client, headers, client_id, account_id, "hist.csv", _monthly_history_csv(today))
    assert resp.json()["imported_count"] == 15

    params: dict[str, Any] = {
        "start": "2024-01-01",
        "end": "2024-09-30",
        "today": today.isoformat(),
    }
    report = client.get(
        f"{API}/clients/{client_id}/analytics/cash-flow", params=params, headers=headers
    )
    assert report.status_code == 200, report.text
    body = report.json()
    signatures = {p["signature"]: p for p in body["recurring_patterns"]}
    assert {"RECEBIMENTO CIELO", "ALUGUEL LOJA", "FOLHA PAGAMENTO"} <= set(signatures)
    assert signatures["ALUGUEL LOJA"]["cadence"] == "MONTHLY"

    # Cada mês queima R$ 1.500: o caixa fica negativo e o motor alerta o gargalo projetado.
    kinds = {a["kind"] for a in body["anomalies"]}
    assert "PROJECTED_NEGATIVE_BALANCE" in kinds or "NEGATIVE_BALANCE" in kinds
    projected_days = [d for d in body["daily"] if d["is_projected"]]
    assert projected_days and projected_days[0]["date"] == "2024-06-21"
    assert Decimal(body["summary"]["closing_balance"]) < 0

    pessimistic = client.get(
        f"{API}/clients/{client_id}/analytics/cash-flow",
        params={**params, "scenario": "PESSIMISTIC"},
        headers=headers,
    ).json()
    assert Decimal(pessimistic["summary"]["closing_balance"]) < Decimal(
        body["summary"]["closing_balance"]
    )
    no_forecast = client.get(
        f"{API}/clients/{client_id}/analytics/cash-flow",
        params={**params, "forecast": False},
        headers=headers,
    ).json()
    assert no_forecast["recurring_patterns"] == []

    dfc = client.get(
        f"{API}/clients/{client_id}/analytics/cash-flow-statement", params=params, headers=headers
    )
    assert dfc.status_code == 200, dfc.text
    periods = dfc.json()["periods"]
    assert len(periods) == 9
    assert periods[-1]["forecast_share"] == "1.0000"
    assert Decimal(periods[-1]["operating_cash_flow"]) == Decimal("-1500.00")
    for prev, cur in itertools.pairwise(periods):
        assert prev["closing_balance"] == cur["opening_balance"]

    bad = client.get(
        f"{API}/clients/{client_id}/analytics/cash-flow",
        params={"start": "2024-02-01", "end": "2024-01-01"},
        headers=headers,
    )
    assert bad.status_code == 422
