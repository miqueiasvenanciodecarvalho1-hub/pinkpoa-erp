"""Ponte entre o motor de categorização (puro) e o banco de dados."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from finance_engine.analysis.categorization import CategorizationEngine, RuleSpec
from finance_engine.models import CategorizationRule, CategorizationSource, Transaction


def load_engine(
    session: Session, organization_id: uuid.UUID, client_id: uuid.UUID
) -> CategorizationEngine:
    rules = session.scalars(
        select(CategorizationRule).where(
            CategorizationRule.organization_id == organization_id,
            CategorizationRule.is_active.is_(True),
            or_(CategorizationRule.client_id.is_(None), CategorizationRule.client_id == client_id),
        )
    )
    return CategorizationEngine(
        RuleSpec(
            id=r.id,
            pattern=r.pattern,
            category_id=r.category_id,
            match_type=r.match_type,
            applies_to=r.applies_to,
            cost_center_id=r.cost_center_id,
            priority=r.priority,
            client_specific=r.client_id is not None,
        )
        for r in rules
    )


def apply_categorization(
    engine: CategorizationEngine,
    transactions: Sequence[Transaction],
    *,
    overwrite: bool = False,
) -> int:
    """Categoriza in-place. Categorização MANUAL nunca é sobrescrita.

    Com ``overwrite=False`` só toca lançamentos ainda sem categoria.
    Retorna quantos lançamentos ficaram com categoria atribuída por regra.
    """
    categorized = 0
    for tx in transactions:
        if tx.categorization_source is CategorizationSource.MANUAL:
            continue
        if not overwrite and tx.category_id is not None:
            continue
        match = engine.match(tx.normalized_description, tx.type)
        if match is None:
            if overwrite and tx.categorization_source is CategorizationSource.RULE:
                tx.category_id = tx.cost_center_id = tx.categorization_rule_id = None
                tx.categorization_source = CategorizationSource.NONE
            continue
        tx.category_id = match.category_id
        tx.cost_center_id = match.cost_center_id or tx.cost_center_id
        tx.categorization_rule_id = match.rule_id
        tx.categorization_source = CategorizationSource.RULE
        categorized += 1
    return categorized


def recategorize_client(
    session: Session,
    organization_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    overwrite: bool = False,
) -> int:
    """Reaplica as regras atuais (ex.: após o consultor cadastrar uma regra nova)."""
    engine = load_engine(session, organization_id, client_id)
    query = select(Transaction).where(
        Transaction.organization_id == organization_id,
        Transaction.client_id == client_id,
        Transaction.categorization_source != CategorizationSource.MANUAL,
    )
    if not overwrite:
        query = query.where(Transaction.category_id.is_(None))
    transactions = list(session.scalars(query))
    count = apply_categorization(engine, transactions, overwrite=overwrite)
    session.flush()
    return count
