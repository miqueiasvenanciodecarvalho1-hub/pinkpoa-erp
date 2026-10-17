from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from finance_engine.api.deps import CurrentClient, CurrentOrg, DbSession
from finance_engine.api.schemas import TransactionCategoryUpdate, TransactionPage, TransactionRead
from finance_engine.models import (
    CategorizationSource,
    Category,
    CostCenter,
    Transaction,
    TransactionType,
)

router = APIRouter(tags=["Lançamentos"])


@router.get("/clients/{client_id}/transactions", response_model=TransactionPage)
def list_transactions(
    db: DbSession,
    client: CurrentClient,
    start: date | None = None,
    end: date | None = None,
    bank_account_id: uuid.UUID | None = None,
    type: TransactionType | None = None,
    category_id: uuid.UUID | None = None,
    uncategorized: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPage:
    filters = [
        Transaction.organization_id == client.organization_id,
        Transaction.client_id == client.id,
    ]
    if start:
        filters.append(Transaction.date >= start)
    if end:
        filters.append(Transaction.date <= end)
    if bank_account_id:
        filters.append(Transaction.bank_account_id == bank_account_id)
    if type:
        filters.append(Transaction.type == type)
    if category_id:
        filters.append(Transaction.category_id == category_id)
    if uncategorized:
        filters.append(Transaction.category_id.is_(None))

    total = db.scalar(select(func.count()).select_from(Transaction).where(*filters)) or 0
    items = db.scalars(
        select(Transaction)
        .where(*filters)
        .order_by(Transaction.date.desc(), Transaction.id)
        .limit(limit)
        .offset(offset)
    )
    return TransactionPage(
        items=[TransactionRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/transactions/{transaction_id}/category", response_model=TransactionRead)
def set_transaction_category(
    transaction_id: uuid.UUID,
    payload: TransactionCategoryUpdate,
    db: DbSession,
    org: CurrentOrg,
) -> Transaction:
    """Categorização manual — passa a ter precedência sobre qualquer regra."""
    tx = db.get(Transaction, transaction_id)
    if tx is None or tx.organization_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lançamento não encontrado")
    if payload.category_id is not None:
        category = db.get(Category, payload.category_id)
        if category is None or category.organization_id != org.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "categoria não encontrada")
    if payload.cost_center_id is not None:
        center = db.get(CostCenter, payload.cost_center_id)
        if center is None or center.client_id != tx.client_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "centro de custo não encontrado")
    tx.category_id = payload.category_id
    tx.cost_center_id = payload.cost_center_id
    tx.categorization_rule_id = None
    tx.categorization_source = (
        CategorizationSource.MANUAL if payload.category_id else CategorizationSource.NONE
    )
    db.flush()
    return tx
