from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from finance_engine.api.deps import CurrentPrincipal, DbSession, MasterOnly
from finance_engine.api.schemas import LoginIn, TokenOut, UserCreate, UserRead
from finance_engine.core.security import create_token, hash_password, verify_password
from finance_engine.models import Client, Role, User

router = APIRouter(prefix="/auth", tags=["Acesso"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: DbSession) -> TokenOut:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "e-mail ou senha incorretos")
    token = create_token(
        {"sub": str(user.id), "org": str(user.organization_id), "role": user.role.value}
    )
    return TokenOut(access_token=token, role=user.role.value, client_id=user.client_id)


@router.get("/me", response_model=UserRead | None)
def me(principal: CurrentPrincipal) -> User | None:
    return principal.user


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, db: DbSession, principal: MasterOnly) -> User:
    role = Role(payload.role)
    if role is Role.CLIENT:
        client = db.get(Client, payload.client_id) if payload.client_id else None
        if client is None or client.organization_id != principal.organization.id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "usuário Cliente precisa de client_id desta organização",
            )
    user = User(
        organization_id=principal.organization.id,
        email=payload.email.lower(),
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=role,
        client_id=payload.client_id if role is Role.CLIENT else None,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "e-mail já cadastrado") from exc
    return user


@router.get("/users", response_model=list[UserRead])
def list_users(db: DbSession, principal: MasterOnly) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.organization_id == principal.organization.id)
            .order_by(User.name)
        )
    )
