"""Dependências FastAPI: sessão de banco e resolução do tenant."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, status
from sqlalchemy.orm import Session, sessionmaker

from finance_engine.db.session import get_session_factory
from finance_engine.models import Client, Organization


def session_factory() -> sessionmaker[Session]:
    """Sobrescrito nos testes via ``app.dependency_overrides``."""
    return get_session_factory()


SessionFactory = Annotated[sessionmaker[Session], Depends(session_factory)]


def get_db(factory: SessionFactory) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_organization(
    db: DbSession,
    x_organization_id: Annotated[uuid.UUID, Header(description="Tenant (organização)")],
) -> Organization:
    """Resolve o tenant da requisição.

    Exemplo simplificado por header. Em produção o ``organization_id`` deve vir de um claim do
    JWT validado (e o usuário deve ter vínculo com a organização) — nunca de entrada livre.
    """
    org = db.get(Organization, x_organization_id)
    if org is None or not org.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organização não encontrada")
    return org


CurrentOrg = Annotated[Organization, Depends(get_organization)]


def get_client(db: DbSession, org: CurrentOrg, client_id: Annotated[uuid.UUID, Path()]) -> Client:
    client = db.get(Client, client_id)
    # 404 (e não 403) para não revelar a existência de clientes de outros tenants.
    if client is None or client.organization_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
    return client


CurrentClient = Annotated[Client, Depends(get_client)]
