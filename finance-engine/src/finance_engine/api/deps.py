"""Dependências FastAPI: sessão de banco, usuário autenticado (RBAC) e escopo de tenant."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, status
from sqlalchemy.orm import Session, sessionmaker

from finance_engine.core.config import get_settings
from finance_engine.core.security import InvalidToken, decode_token
from finance_engine.db.session import get_session_factory
from finance_engine.models import Client, Organization, Role, User


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


@dataclass(frozen=True, slots=True)
class Principal:
    """Quem está chamando: organização, papel e (para CLIENT) o único cliente visível."""

    organization: Organization
    role: Role
    user: User | None = None
    client_id: uuid.UUID | None = None

    @property
    def is_client(self) -> bool:
        return self.role is Role.CLIENT


def get_principal(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    x_organization_id: Annotated[
        uuid.UUID | None, Header(description="Só em desenvolvimento")
    ] = None,
) -> Principal:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "faça login", headers={"WWW-Authenticate": "Bearer"}
    )
    if authorization and authorization.lower().startswith("bearer "):
        try:
            claims = decode_token(authorization[7:].strip())
        except InvalidToken as exc:
            raise unauthorized from exc
        user = db.get(User, uuid.UUID(claims["sub"]))
        if user is None or not user.is_active:
            raise unauthorized
        org = db.get(Organization, user.organization_id)
        if org is None or not org.is_active:
            raise unauthorized
        return Principal(org, user.role, user, user.client_id)
    if x_organization_id and get_settings().allow_header_tenant:
        org = db.get(Organization, x_organization_id)
        if org is None or not org.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "organização não encontrada")
        return Principal(org, Role.MASTER)
    raise unauthorized


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_organization(principal: CurrentPrincipal) -> Organization:
    return principal.organization


CurrentOrg = Annotated[Organization, Depends(get_organization)]


def require_staff(principal: CurrentPrincipal) -> Principal:
    """Consultor ou Master. O cliente (empresário) não altera configurações nem dados alheios."""
    if principal.is_client:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ação restrita à consultoria")
    return principal


def require_master(principal: CurrentPrincipal) -> Principal:
    if principal.role is not Role.MASTER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ação restrita ao perfil Master")
    return principal


StaffOnly = Annotated[Principal, Depends(require_staff)]
MasterOnly = Annotated[Principal, Depends(require_master)]


def get_client(
    db: DbSession, principal: CurrentPrincipal, client_id: Annotated[uuid.UUID, Path()]
) -> Client:
    client = db.get(Client, client_id)
    # 404 (e não 403) para não revelar a existência de clientes de outros tenants.
    if client is None or client.organization_id != principal.organization.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
    if principal.is_client and principal.client_id != client.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
    return client


CurrentClient = Annotated[Client, Depends(get_client)]
