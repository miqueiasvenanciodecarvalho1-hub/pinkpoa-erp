"""Clientes HTTP dos serviços públicos. Cada provedor devolve o registro já padronizado.

Os provedores são intercambiáveis e ordenados no ``LookupService``: quando um falha (fora do ar,
limite de requisições, formato inesperado), o próximo é tentado. ``NotFound`` encerra a busca
apenas quando o provedor afirma que o documento não existe.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from finance_engine.lookup.models import (
    Activity,
    Address,
    Bank,
    CompanyRecord,
    Holiday,
    RegisteredPartner,
)

JSON = dict[str, Any]


class ProviderError(Exception):
    """Falha transitória ou resposta inválida: tente o próximo provedor."""


class NotFound(Exception):
    """O provedor respondeu que o documento não existe."""


def _get_json(client: httpx.Client, url: str) -> Any:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{url}: {exc}") from exc
    if response.status_code in (400, 404):
        raise NotFound(url)
    if response.status_code != 200:
        raise ProviderError(f"{url}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{url}: resposta não é JSON") from exc


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _cnae(code: Any) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(7)


def _partner_kind(document: str, explicit: str = "") -> str:
    text = explicit.upper()
    if "JUR" in text or len("".join(ch for ch in document if ch.isdigit())) == 14:
        return "PJ"
    if "EXTERIOR" in text or "ESTRANGEIRO" in text:
        return "EXTERIOR"
    return "PF"


def _now() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------------- CNPJ


def cnpj_brasilapi(client: httpx.Client, cnpj: str) -> CompanyRecord:
    d = _get_json(client, f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
    if not isinstance(d, dict) or "razao_social" not in d:
        raise ProviderError("brasilapi: formato inesperado")
    street = " ".join(p for p in (d.get("descricao_tipo_de_logradouro"), d.get("logradouro")) if p)
    return CompanyRecord(
        cnpj=cnpj,
        legal_name=d.get("razao_social") or "",
        trade_name=d.get("nome_fantasia") or "",
        status=(d.get("descricao_situacao_cadastral") or "").upper(),
        status_date=_date(d.get("data_situacao_cadastral")),
        opened_at=_date(d.get("data_inicio_atividade")),
        legal_nature=d.get("natureza_juridica") or "",
        size=d.get("porte") or "",
        share_capital=_money(d.get("capital_social")),
        simples=d.get("opcao_pelo_simples"),
        mei=d.get("opcao_pelo_mei"),
        main_activity=Activity(_cnae(d.get("cnae_fiscal")), d.get("cnae_fiscal_descricao") or "")
        if d.get("cnae_fiscal")
        else None,
        secondary_activities=[
            Activity(_cnae(a.get("codigo")), a.get("descricao") or "")
            for a in d.get("cnaes_secundarios") or []
            if a.get("codigo")
        ],
        address=Address(
            cep=str(d.get("cep") or ""),
            street=street,
            number=str(d.get("numero") or ""),
            complement=d.get("complemento") or "",
            district=d.get("bairro") or "",
            city=d.get("municipio") or "",
            state=d.get("uf") or "",
            ibge_code=str(d["codigo_municipio_ibge"]) if d.get("codigo_municipio_ibge") else None,
            source="brasilapi",
        ),
        phone=d.get("ddd_telefone_1") or "",
        email=d.get("email") or "",
        head_office=str(d.get("identificador_matriz_filial")) == "1",
        partners=[
            RegisteredPartner(
                name=s.get("nome_socio") or "",
                document=s.get("cnpj_cpf_do_socio") or "",
                kind=_partner_kind(
                    s.get("cnpj_cpf_do_socio") or "", str(s.get("identificador_de_socio"))
                ),
                role=s.get("qualificacao_socio") or "",
                since=_date(s.get("data_entrada_sociedade")),
            )
            for s in d.get("qsa") or []
        ],
        source="BrasilAPI (Receita Federal)",
        fetched_at=_now(),
    )


def cnpj_cnpjws(client: httpx.Client, cnpj: str) -> CompanyRecord:
    d = _get_json(client, f"https://publica.cnpj.ws/cnpj/{cnpj}")
    est = d.get("estabelecimento") if isinstance(d, dict) else None
    if not isinstance(est, dict):
        raise ProviderError("cnpj.ws: formato inesperado")
    main = est.get("atividade_principal") or {}
    simples = d.get("simples") or {}
    return CompanyRecord(
        cnpj=cnpj,
        legal_name=d.get("razao_social") or "",
        trade_name=est.get("nome_fantasia") or "",
        status=(est.get("situacao_cadastral") or "").upper(),
        status_date=_date(est.get("data_situacao_cadastral")),
        opened_at=_date(est.get("data_inicio_atividade")),
        legal_nature=(d.get("natureza_juridica") or {}).get("descricao") or "",
        size=(d.get("porte") or {}).get("descricao") or "",
        share_capital=_money(d.get("capital_social")),
        simples=(simples.get("simples") == "Sim") if simples else None,
        mei=(simples.get("mei") == "Sim") if simples else None,
        main_activity=Activity(
            _cnae(main.get("subclasse") or main.get("id")), main.get("descricao") or ""
        )
        if main
        else None,
        secondary_activities=[
            Activity(_cnae(a.get("subclasse") or a.get("id")), a.get("descricao") or "")
            for a in est.get("atividades_secundarias") or []
        ],
        address=Address(
            cep=str(est.get("cep") or ""),
            street=" ".join(p for p in (est.get("tipo_logradouro"), est.get("logradouro")) if p),
            number=str(est.get("numero") or ""),
            complement=est.get("complemento") or "",
            district=est.get("bairro") or "",
            city=(est.get("cidade") or {}).get("nome") or "",
            state=(est.get("estado") or {}).get("sigla") or "",
            ibge_code=str((est.get("cidade") or {}).get("ibge_id") or "") or None,
            source="cnpj.ws",
        ),
        phone=f"{est.get('ddd1') or ''}{est.get('telefone1') or ''}",
        email=est.get("email") or "",
        head_office=(est.get("tipo") or "").lower() == "matriz",
        partners=[
            RegisteredPartner(
                name=s.get("nome") or "",
                document=s.get("cpf_cnpj_socio") or "",
                kind=_partner_kind(s.get("cpf_cnpj_socio") or "", s.get("tipo") or ""),
                role=(s.get("qualificacao_socio") or {}).get("descricao", "").strip(),
                since=_date(s.get("data_entrada")),
            )
            for s in d.get("socios") or []
        ],
        source="CNPJ.ws (Receita Federal)",
        fetched_at=_now(),
    )


def cnpj_cnpja(client: httpx.Client, cnpj: str) -> CompanyRecord:
    d = _get_json(client, f"https://open.cnpja.com/office/{cnpj}")
    company = d.get("company") if isinstance(d, dict) else None
    if not isinstance(company, dict):
        raise ProviderError("cnpja: formato inesperado")
    addr = d.get("address") or {}
    main = d.get("mainActivity") or {}
    phones = d.get("phones") or [{}]
    emails = d.get("emails") or [{}]
    simples = company.get("simples") or {}
    simei = company.get("simei") or {}
    return CompanyRecord(
        cnpj=cnpj,
        legal_name=company.get("name") or "",
        trade_name=d.get("alias") or "",
        status=((d.get("status") or {}).get("text") or "").upper(),
        status_date=_date(d.get("statusDate")),
        opened_at=_date(d.get("founded")),
        legal_nature=(company.get("nature") or {}).get("text") or "",
        size=(company.get("size") or {}).get("text") or "",
        share_capital=_money(company.get("equity")),
        simples=simples.get("optant"),
        mei=simei.get("optant"),
        main_activity=Activity(_cnae(main.get("id")), main.get("text") or "") if main else None,
        secondary_activities=[
            Activity(_cnae(a.get("id")), a.get("text") or "") for a in d.get("sideActivities") or []
        ],
        address=Address(
            cep=str(addr.get("zip") or ""),
            street=addr.get("street") or "",
            number=str(addr.get("number") or ""),
            complement=addr.get("details") or "",
            district=addr.get("district") or "",
            city=addr.get("city") or "",
            state=addr.get("state") or "",
            ibge_code=str(addr.get("municipality") or "") or None,
            source="cnpja",
        ),
        phone=f"{phones[0].get('area') or ''}{phones[0].get('number') or ''}",
        email=emails[0].get("address") or "",
        head_office=bool(d.get("head")),
        partners=[
            RegisteredPartner(
                name=(m.get("person") or {}).get("name") or "",
                document=(m.get("person") or {}).get("taxId") or "",
                kind={"NATURAL": "PF", "LEGAL": "PJ", "FOREIGN": "EXTERIOR"}.get(
                    (m.get("person") or {}).get("type") or "", "PF"
                ),
                role=(m.get("role") or {}).get("text") or "",
                since=_date(m.get("since")),
            )
            for m in company.get("members") or []
            if (m.get("person") or {}).get("type") != "UNKNOWN"
        ],
        source="CNPJá (Receita Federal)",
        fetched_at=_now(),
    )


# ----------------------------------------------------------------------------- CEP


def cep_brasilapi(client: httpx.Client, cep: str) -> Address:
    d = _get_json(client, f"https://brasilapi.com.br/api/cep/v2/{cep}")
    if not isinstance(d, dict) or "city" not in d:
        raise ProviderError("brasilapi cep: formato inesperado")
    return Address(
        cep=cep,
        street=d.get("street") or "",
        district=d.get("neighborhood") or "",
        city=d.get("city") or "",
        state=d.get("state") or "",
        ibge_code=((d.get("ibge") or {}).get("city")) or None,
        source="BrasilAPI",
    )


def cep_viacep(client: httpx.Client, cep: str) -> Address:
    d = _get_json(client, f"https://viacep.com.br/ws/{cep}/json/")
    if not isinstance(d, dict):
        raise ProviderError("viacep: formato inesperado")
    if d.get("erro"):
        raise NotFound(cep)
    return Address(
        cep=cep,
        street=d.get("logradouro") or "",
        complement=d.get("complemento") or "",
        district=d.get("bairro") or "",
        city=d.get("localidade") or "",
        state=d.get("uf") or "",
        ibge_code=d.get("ibge") or None,
        source="ViaCEP",
    )


def cep_opencep(client: httpx.Client, cep: str) -> Address:
    d = _get_json(client, f"https://opencep.com/v1/{cep}")
    if not isinstance(d, dict) or "localidade" not in d:
        raise ProviderError("opencep: formato inesperado")
    return Address(
        cep=cep,
        street=d.get("logradouro") or "",
        complement=d.get("complemento") or "",
        district=d.get("bairro") or "",
        city=d.get("localidade") or "",
        state=d.get("uf") or "",
        ibge_code=d.get("ibge") or None,
        source="OpenCEP",
    )


# ----------------------------------------------------------------------------- bancos e feriados


def banks_brasilapi(client: httpx.Client) -> list[Bank]:
    data = _get_json(client, "https://brasilapi.com.br/api/banks/v1")
    if not isinstance(data, list):
        raise ProviderError("brasilapi banks: formato inesperado")
    return sorted(
        (
            Bank(
                code=str(b["code"]).zfill(3),
                ispb=str(b.get("ispb") or ""),
                name=b.get("name") or "",
                full_name=b.get("fullName") or "",
            )
            for b in data
            if b.get("code") is not None
        ),
        key=lambda b: b.code,
    )


def holidays_brasilapi(client: httpx.Client, year: int) -> list[Holiday]:
    data = _get_json(client, f"https://brasilapi.com.br/api/feriados/v1/{year}")
    if not isinstance(data, list):
        raise ProviderError("brasilapi feriados: formato inesperado")
    out: list[Holiday] = []
    for h in data:
        day = _date(h.get("date"))
        if day:
            out.append(Holiday(day, h.get("name") or "", h.get("type") or "national"))
    return out


CnpjProvider = Callable[[httpx.Client, str], CompanyRecord]
CepProvider = Callable[[httpx.Client, str], Address]
CNPJ_PROVIDERS: tuple[CnpjProvider, ...] = (cnpj_brasilapi, cnpj_cnpjws, cnpj_cnpja)
CEP_PROVIDERS: tuple[CepProvider, ...] = (cep_brasilapi, cep_viacep, cep_opencep)
