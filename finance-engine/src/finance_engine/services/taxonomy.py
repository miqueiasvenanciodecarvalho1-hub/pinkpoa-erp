"""Provisionamento do plano de categorias e regras padrão de uma organização."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_engine.analysis.categorization import DEFAULT_TAXONOMY, DefaultCategory
from finance_engine.models import CategorizationRule, Category, MatchType


def seed_default_taxonomy(
    session: Session,
    organization_id: uuid.UUID,
    taxonomy: tuple[DefaultCategory, ...] = DEFAULT_TAXONOMY,
) -> int:
    """Cria categorias e regras padrão que ainda não existem. Idempotente.

    Retorna a quantidade de regras criadas.
    """
    existing_categories = {
        c.name: c
        for c in session.scalars(
            select(Category).where(Category.organization_id == organization_id)
        )
    }
    existing_rules = {
        (r.category_id, r.pattern)
        for r in session.scalars(
            select(CategorizationRule).where(
                CategorizationRule.organization_id == organization_id,
                CategorizationRule.client_id.is_(None),
            )
        )
    }
    created = 0
    for item in taxonomy:
        category = existing_categories.get(item.name)
        if category is None:
            category = Category(organization_id=organization_id, name=item.name, group=item.group)
            session.add(category)
            session.flush()
            existing_categories[item.name] = category
        for pattern in item.patterns:
            if (category.id, pattern) in existing_rules:
                continue
            session.add(
                CategorizationRule(
                    organization_id=organization_id,
                    pattern=pattern,
                    match_type=MatchType.CONTAINS,
                    applies_to=item.applies_to,
                    category_id=category.id,
                    priority=item.priority,
                )
            )
            existing_rules.add((category.id, pattern))
            created += 1
    session.flush()
    return created
