from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from finance_engine.api.deps import CurrentClient, CurrentOrg, DbSession, StaffOnly
from finance_engine.api.schemas import CategoryRead, RecategorizeResult, RuleCreate, RuleRead
from finance_engine.models import CategorizationRule, Category, Client, MatchType
from finance_engine.services.categorization_service import recategorize_client

router = APIRouter(tags=["Categorização"])


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(db: DbSession, org: CurrentOrg) -> list[Category]:
    return list(
        db.scalars(
            select(Category).where(Category.organization_id == org.id).order_by(Category.name)
        )
    )


@router.get("/categorization-rules", response_model=list[RuleRead])
def list_rules(db: DbSession, org: CurrentOrg) -> list[CategorizationRule]:
    return list(
        db.scalars(
            select(CategorizationRule)
            .where(CategorizationRule.organization_id == org.id)
            .order_by(CategorizationRule.priority.desc(), CategorizationRule.pattern)
        )
    )


@router.post("/categorization-rules", response_model=RuleRead, status_code=201)
def create_rule(
    payload: RuleCreate, db: DbSession, org: CurrentOrg, _: StaffOnly
) -> CategorizationRule:
    """Ex.: ``{"pattern": "FORNECEDOR X", "category_id": <Custo de Mercadorias>}``."""
    category = db.get(Category, payload.category_id)
    if category is None or category.organization_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "categoria não encontrada")
    if payload.client_id is not None:
        client = db.get(Client, payload.client_id)
        if client is None or client.organization_id != org.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
    if payload.match_type is MatchType.REGEX:
        try:
            re.compile(payload.pattern)
        except re.error as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"regex inválida: {exc}"
            ) from exc

    rule = CategorizationRule(organization_id=org.id, **payload.model_dump(exclude={"apply_now"}))
    db.add(rule)
    db.flush()
    if payload.apply_now:
        clients = (
            [payload.client_id]
            if payload.client_id
            else list(db.scalars(select(Client.id).where(Client.organization_id == org.id)))
        )
        for client_id in clients:
            recategorize_client(db, org.id, client_id)
    return rule


@router.post("/clients/{client_id}/recategorize", response_model=RecategorizeResult)
def recategorize(
    db: DbSession, client: CurrentClient, _: StaffOnly, overwrite: bool = False
) -> RecategorizeResult:
    """Reaplica as regras. ``overwrite=true`` refaz inclusive o que já foi categorizado por regra
    (categorizações manuais são sempre preservadas)."""
    count = recategorize_client(db, client.organization_id, client.id, overwrite=overwrite)
    return RecategorizeResult(categorized=count)
