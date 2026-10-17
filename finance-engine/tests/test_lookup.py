"""Consultas cadastrais com transporte HTTP simulado (sem rede)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from finance_engine.core.exceptions import NotFoundError
from finance_engine.lookup import InvalidDocumentError, LookupService, LookupUnavailableError
from finance_engine.lookup.service import get_lookup_service

CNPJ = "11222333000181"
BRASILAPI_CNPJ = {
    "cnpj": CNPJ,
    "razao_social": "LOJA EXEMPLO LTDA",
    "nome_fantasia": "LOJA EXEMPLO",
    "descricao_situacao_cadastral": "ATIVA",
    "data_situacao_cadastral": "2015-03-02",
    "data_inicio_atividade": "2015-03-02",
    "natureza_juridica": "Sociedade Empresária Limitada",
    "porte": "EMPRESA DE PEQUENO PORTE",
    "capital_social": 150000,
    "opcao_pelo_simples": True,
    "opcao_pelo_mei": False,
    "cnae_fiscal": 4781400,
    "cnae_fiscal_descricao": "Comércio varejista de artigos do vestuário e acessórios",
    "cnaes_secundarios": [{"codigo": 1412601, "descricao": "Confecção de peças do vestuário"}],
    "descricao_tipo_de_logradouro": "AVENIDA",
    "logradouro": "PAULISTA",
    "numero": "1000",
    "complemento": "LOJA 2",
    "bairro": "BELA VISTA",
    "municipio": "SAO PAULO",
    "uf": "SP",
    "cep": "01310100",
    "codigo_municipio_ibge": 3550308,
    "ddd_telefone_1": "1133334444",
    "email": None,
    "identificador_matriz_filial": 1,
    "qsa": [
        {
            "nome_socio": "MARCOS VIEIRA",
            "cnpj_cpf_do_socio": "***123456**",
            "identificador_de_socio": 2,
            "qualificacao_socio": "Sócio-Administrador",
            "data_entrada_sociedade": "2015-03-02",
        },
        {
            "nome_socio": "EXEMPLO PARTICIPACOES SA",
            "cnpj_cpf_do_socio": "58214036000158",
            "identificador_de_socio": 1,
            "qualificacao_socio": "Sócio",
            "data_entrada_sociedade": "2020-01-10",
        },
    ],
}


def service(handler: Callable[[httpx.Request], httpx.Response]) -> LookupService:
    return LookupService(httpx.Client(transport=httpx.MockTransport(handler)))


def test_cnpj_brasilapi_normalized_and_enriched() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        return httpx.Response(200, json=BRASILAPI_CNPJ)

    svc = service(handler)
    rec = svc.company("11.222.333/0001-81")
    assert rec.legal_name == "LOJA EXEMPLO LTDA"
    assert rec.share_capital == Decimal("150000.00")
    assert rec.address and rec.address.street == "AVENIDA PAULISTA" and rec.address.state == "SP"
    assert rec.main_activity and rec.main_activity.code == "4781400"
    assert [p.kind for p in rec.partners] == ["PF", "PJ"]
    assert rec.suggested_sector == "Comércio" and "COM" in rec.suggested_specialties
    assert any("Simples" in a for a in rec.alerts)
    assert any("pessoa jurídica" in a for a in rec.alerts)
    svc.company(CNPJ)  # cache: não consulta de novo
    assert calls == ["brasilapi.com.br"]


def test_cnpj_falls_back_to_next_provider() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "brasilapi.com.br":
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "razao_social": "LOJA EXEMPLO LTDA",
                "capital_social": "150000.00",
                "natureza_juridica": {"descricao": "Sociedade Empresária Limitada"},
                "porte": {"descricao": "Micro Empresa"},
                "simples": {"simples": "Não", "mei": "Não"},
                "socios": [
                    {
                        "nome": "MARCOS VIEIRA",
                        "cpf_cnpj_socio": "***123456**",
                        "tipo": "Pessoa Física",
                        "qualificacao_socio": {"descricao": "Sócio-Administrador"},
                        "data_entrada": "2015-03-02",
                    }
                ],
                "estabelecimento": {
                    "situacao_cadastral": "Ativa",
                    "tipo": "Matriz",
                    "cep": "01310100",
                    "logradouro": "PAULISTA",
                    "tipo_logradouro": "Avenida",
                    "numero": "1000",
                    "cidade": {"nome": "São Paulo", "ibge_id": 3550308},
                    "estado": {"sigla": "SP"},
                    "atividade_principal": {"subclasse": "4781-4/00", "descricao": "Vestuário"},
                },
            },
        )

    rec = service(handler).company(CNPJ)
    assert rec.source.startswith("CNPJ.ws")
    assert rec.status == "ATIVA" and rec.main_activity and rec.main_activity.code == "4781400"
    assert rec.partners[0].role == "Sócio-Administrador"


def test_cnpj_errors() -> None:
    with pytest.raises(InvalidDocumentError):
        service(lambda r: httpx.Response(200)).company("11.222.333/0001-00")
    with pytest.raises(NotFoundError):
        service(lambda r: httpx.Response(404)).company(CNPJ)
    with pytest.raises(LookupUnavailableError):
        service(lambda r: httpx.Response(503)).company(CNPJ)


def test_cep_fallback_viacep_after_brasilapi_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "brasilapi.com.br":
            raise httpx.ConnectError("reset")
        return httpx.Response(
            200,
            json={
                "cep": "01310-100",
                "logradouro": "Avenida Paulista",
                "bairro": "Bela Vista",
                "localidade": "São Paulo",
                "uf": "SP",
                "ibge": "3550308",
            },
        )

    addr = service(handler).address("01310-100")
    assert (addr.street, addr.city, addr.state, addr.source) == (
        "Avenida Paulista",
        "São Paulo",
        "SP",
        "ViaCEP",
    )
    with pytest.raises(InvalidDocumentError):
        service(handler).address("123")


def test_lookup_api_and_client_from_cnpj(client: TestClient) -> None:
    svc = service(lambda r: httpx.Response(200, json=BRASILAPI_CNPJ))
    client.app.dependency_overrides[get_lookup_service] = lambda: svc  # type: ignore[attr-defined]
    r = client.get("/api/v1/lookup/cnpj/11.222.333/0001-81")  # com máscara (tem "/")
    assert r.status_code == 200 and r.json()["partners"][1]["kind"] == "PJ"
    assert client.get("/api/v1/lookup/cnpj/123").status_code == 422

    org = client.post(
        "/api/v1/organizations", json={"name": "Consultoria", "slug": "consult"}
    ).json()
    created = client.post(
        "/api/v1/clients/from-cnpj",
        json={"cnpj": "11.222.333/0001-81"},
        headers={"X-Organization-Id": org["id"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "LOJA EXEMPLO LTDA" and body["trade_name"] == "LOJA EXEMPLO"
    assert body["registry_data"]["address"]["city"] == "SAO PAULO"
