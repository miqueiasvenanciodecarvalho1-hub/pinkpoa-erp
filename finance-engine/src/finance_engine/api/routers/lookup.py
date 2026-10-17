"""Consultas cadastrais para preencher formulários automaticamente."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from finance_engine.api.schemas import AddressOut, BankOut, CompanyRecordOut, HolidayOut
from finance_engine.lookup import (
    Address,
    Bank,
    CompanyRecord,
    Holiday,
    LookupService,
    get_lookup_service,
)

router = APIRouter(prefix="/lookup", tags=["Consultas cadastrais"])
Lookup = Annotated[LookupService, Depends(get_lookup_service)]


@router.get("/cnpj/{cnpj:path}", response_model=CompanyRecordOut)
def company(
    cnpj: Annotated[str, Path(description="Com ou sem máscara")], lookup: Lookup
) -> CompanyRecord:
    """Razão social, fantasia, situação, CNAE, endereço, Simples/MEI e sócios (QSA), com alertas
    e sugestão de setor/especialidades. Fontes: BrasilAPI → CNPJ.ws → CNPJá (Receita Federal)."""
    return lookup.company(cnpj)


@router.get("/cep/{cep}", response_model=AddressOut)
def address(cep: str, lookup: Lookup) -> Address:
    """Endereço pelo CEP. Fontes: BrasilAPI → ViaCEP → OpenCEP."""
    return lookup.address(cep)


@router.get("/banks", response_model=list[BankOut])
def banks(lookup: Lookup) -> list[Bank]:
    """Bancos com código COMPE para escolher a conta pelo nome."""
    return lookup.banks()


@router.get("/holidays/{year}", response_model=list[HolidayOut])
def holidays(year: int, lookup: Lookup) -> list[Holiday]:
    """Feriados nacionais (usados para projetar dias úteis no fluxo de caixa)."""
    return lookup.holidays(year)
