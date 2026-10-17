from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from finance_engine.api.deps import CurrentClient, CurrentOrg, DbSession
from finance_engine.api.schemas import (
    BankAccountCreate,
    BankAccountRead,
    ClientCreate,
    ClientRead,
    OrganizationCreate,
    OrganizationRead,
)
from finance_engine.models import BankAccount, Client, Organization
from finance_engine.services.taxonomy import seed_default_taxonomy

router = APIRouter(tags=["Cadastros"])


@router.post("/organizations", response_model=OrganizationRead, status_code=201)
def create_organization(payload: OrganizationCreate, db: DbSession) -> Organization:
    """Bootstrap de tenant (em produção: restrito ao backoffice/superadmin)."""
    org = Organization(name=payload.name, slug=payload.slug)
    db.add(org)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "slug já utilizado") from exc
    if payload.seed_default_taxonomy:
        seed_default_taxonomy(db, org.id)
    return org


@router.post("/clients", response_model=ClientRead, status_code=201)
def create_client(payload: ClientCreate, db: DbSession, org: CurrentOrg) -> Client:
    client = Client(organization_id=org.id, **payload.model_dump())
    db.add(client)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "documento já cadastrado") from exc
    return client


@router.get("/clients", response_model=list[ClientRead])
def list_clients(db: DbSession, org: CurrentOrg) -> list[Client]:
    return list(
        db.scalars(select(Client).where(Client.organization_id == org.id).order_by(Client.name))
    )


@router.post("/clients/{client_id}/bank-accounts", response_model=BankAccountRead, status_code=201)
def create_bank_account(
    payload: BankAccountCreate, db: DbSession, client: CurrentClient
) -> BankAccount:
    account = BankAccount(
        organization_id=client.organization_id, client_id=client.id, **payload.model_dump()
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "conta já cadastrada") from exc
    return account


@router.get("/clients/{client_id}/bank-accounts", response_model=list[BankAccountRead])
def list_bank_accounts(db: DbSession, client: CurrentClient) -> list[BankAccount]:
    return list(db.scalars(select(BankAccount).where(BankAccount.client_id == client.id)))
