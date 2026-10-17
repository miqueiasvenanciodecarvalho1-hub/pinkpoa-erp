"""Papéis de acesso, contas a pagar/receber, conciliação, demonstrativos e indicadores."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_api import API, CNPJ, _setup, _upload
from tests.test_finance_tools import BALANCE, DRE

TITLES = (
    "Cliente;CNPJ;Documento;Emissão;Vencimento;Valor;Saldo;Pagamento\n"
    "Mercado Bom Preço;11.222.333/0001-81;NF 10;01/02/2024;10/03/2024;1.500,00;;\n"
    "Padaria Sol;;NF 11;01/02/2024;12/03/2024;300,00;;\n"
    "Padaria Sol;;NF 12;01/02/2024;15/03/2024;200,00;;\n"
    "Atacado Leste;;NF 13;01/01/2024;01/02/2024;800,00;0,00;11/02/2024\n"
    "Cliente Atrasado;;NF 14;01/12/2023;05/01/2024;700,00;;\n"
).encode()

BANK = (
    "Data;Histórico;Documento;Valor\n"
    "11/03/2024;PIX RECEBIDO MERCADO BOM PRECO;1;1.500,00\n"
    "15/03/2024;TED PADARIA SOL LTDA;2;500,00\n"
    "16/03/2024;TARIFA PACOTE;3;-45,00\n"
).encode()


def _org_with_master(client: TestClient) -> tuple[dict[str, str], str]:
    org = client.post(
        f"{API}/organizations",
        json={
            "name": "MVC Carvalho",
            "slug": "mvc",
            "admin_email": "Socio@MVC.com.br",
            "admin_name": "Sócio",
            "admin_password": "senha-forte-1",
        },
    )
    assert org.status_code == 201, org.text
    tok = client.post(
        f"{API}/auth/login", json={"email": "socio@mvc.com.br", "password": "senha-forte-1"}
    )
    assert tok.status_code == 200, tok.text
    assert tok.json()["role"] == "MASTER"
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, org.json()["id"]


def test_login_roles_and_client_scope(client: TestClient) -> None:
    master, _ = _org_with_master(client)
    assert (
        client.post(
            f"{API}/auth/login", json={"email": "socio@mvc.com.br", "password": "x"}
        ).status_code
        == 401
    )
    assert client.get(f"{API}/clients", headers={"Authorization": "Bearer lixo"}).status_code == 401

    mine = client.post(
        f"{API}/clients",
        json={"person_type": "PJ", "name": "Loja A", "document": CNPJ},
        headers=master,
    ).json()["id"]
    other = client.post(
        f"{API}/clients",
        json={"person_type": "PF", "name": "Outro", "document": "529.982.247-25"},
        headers=master,
    ).json()["id"]
    bad = client.post(
        f"{API}/auth/users",
        json={
            "email": "dono@loja.com",
            "name": "Dono",
            "password": "senha-cliente",
            "role": "CLIENT",
        },
        headers=master,
    )
    assert bad.status_code == 422  # cliente sem client_id
    ok = client.post(
        f"{API}/auth/users",
        json={
            "email": "dono@loja.com",
            "name": "Dono",
            "password": "senha-cliente",
            "role": "CLIENT",
            "client_id": mine,
        },
        headers=master,
    )
    assert ok.status_code == 201, ok.text
    tok = client.post(
        f"{API}/auth/login", json={"email": "dono@loja.com", "password": "senha-cliente"}
    ).json()
    owner = {"Authorization": f"Bearer {tok['access_token']}"}

    assert [c["id"] for c in client.get(f"{API}/clients", headers=owner).json()] == [mine]
    assert client.get(f"{API}/clients/{mine}/titles", headers=owner).status_code == 200
    assert client.get(f"{API}/clients/{other}/titles", headers=owner).status_code == 404
    assert client.get(f"{API}/auth/users", headers=owner).status_code == 403
    staff_only = client.post(f"{API}/clients/{mine}/reconciliation/apply", json={}, headers=owner)
    assert staff_only.status_code == 403
    assert client.get(f"{API}/auth/me", headers=owner).json()["role"] == "CLIENT"


def test_titles_aging_reconciliation_and_anticipation(client: TestClient) -> None:
    headers, client_id, account_id = _setup(client)
    url = f"{API}/clients/{client_id}"
    imp = client.post(
        f"{url}/titles/import",
        files={"file": ("receber.csv", TITLES)},
        data={"kind": "RECEIVABLE"},
        headers=headers,
    )
    assert imp.status_code == 201, imp.text
    assert imp.json() == {"created": 5, "updated": 0, "errors": []}
    again = client.post(
        f"{url}/titles/import", files={"file": ("receber.csv", TITLES)}, headers=headers
    )
    assert again.json()["created"] == 0 and again.json()["updated"] == 5

    assert len(client.get(f"{url}/titles", headers=headers).json()) == 4  # 1 quitado
    aging = client.get(
        f"{url}/titles/aging", params={"reference_date": "2024-03-01"}, headers=headers
    ).json()
    assert aging["total_open"] == "2700.00"
    assert aging["total_overdue"] == "700.00"

    assert _upload(client, headers, client_id, account_id, "mar.csv", BANK).status_code == 201
    sugg = client.get(
        f"{url}/reconciliation/suggestions", params={"today": "2024-03-20"}, headers=headers
    )
    assert sugg.status_code == 200, sugg.text
    groups = sorted(len(s["title_ids"]) for s in sugg.json())
    assert groups == [1, 2]
    applied = client.post(
        f"{url}/reconciliation/apply",
        json={"matches": sugg.json()},
        params={"today": "2024-03-20"},
        headers=headers,
    )
    assert applied.json() == {"applied": 3}  # vínculos título × lançamento
    left = client.get(f"{url}/titles", headers=headers).json()
    assert [t["counterparty"] for t in left] == ["Cliente Atrasado"]
    assert (
        client.get(
            f"{url}/reconciliation/suggestions", params={"today": "2024-03-20"}, headers=headers
        ).json()
        == []
    )

    sim = client.post(
        f"{API}/tools/anticipation",
        json={
            "monthly_rate": "0.025",
            "operation_date": "2024-01-01",
            "receivables": [{"amount": "10000", "due_date": "2024-03-01"}],
            "alternative_monthly_rate": "0.018",
        },
        headers=headers,
    )
    assert sim.status_code == 200, sim.text
    body = sim.json()
    assert body["titles"][0]["days"] == 61
    assert float(body["effective_monthly_rate"]) > 0.025
    assert body["recommendation"]


def test_statements_review_and_indicators(client: TestClient) -> None:
    headers, client_id, _ = _setup(client)
    url = f"{API}/clients/{client_id}"
    csv = "\n".join(f"{d};{v}" for d, v in BALANCE + DRE).encode()
    up = client.post(f"{url}/statements", files={"file": ("balanco.csv", csv)}, headers=headers)
    assert up.status_code == 201, up.text
    (st,) = up.json()
    assert st["status"] == "DRAFT"
    assert st["values"]["AC_CLIENTES"] == "300000.00"

    ind = client.get(f"{url}/analytics/indicators", headers=headers).json()
    assert ind["accounting"]["pmr"] == "45.0"
    assert ind["accounting"]["ncg"] == "270000.00"
    assert any("tesouraria" in a for a in ind["accounting"]["alerts"])

    fix = client.patch(
        f"{url}/statements/{st['id']}",
        json={"values": {"AC_CLIENTES": "180000"}, "validate": True},
        headers=headers,
    )
    assert fix.status_code == 200, fix.text
    assert fix.json()["status"] == "VALIDATED"
    ind = client.get(f"{url}/analytics/indicators", headers=headers).json()
    assert ind["accounting"]["pmr"] == "27.0"
    bad = client.post(f"{url}/statements", files={"file": ("x.bin", b"\x00\x01")}, headers=headers)
    assert bad.status_code == 422
