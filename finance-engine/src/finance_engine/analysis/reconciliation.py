"""Conciliação automática: casa lançamentos do extrato com títulos em aberto.

Pontuação por evidências (0..1):
* valor: igual ao centavo (forte) ou dentro de uma tolerância de taxas/juros (média);
* data: lançamento perto do vencimento (pagamentos atrasam; antecipação é rara);
* texto: nome da contraparte, número do documento ou CNPJ/CPF aparecendo no histórico.

Casamentos 1:1 vêm primeiro (atribuição gulosa pela maior pontuação, sem reutilizar títulos ou
lançamentos). Depois procura N:1: um lançamento que liquida 2 ou 3 títulos da mesma contraparte
(ex.: cliente paga várias duplicatas num PIX só).
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from finance_engine.ingestion.normalization import normalize_text

ZERO = Decimal("0")
_STOP = {
    "LTDA",
    "ME",
    "EPP",
    "SA",
    "S/A",
    "EIRELI",
    "DE",
    "DA",
    "DO",
    "DOS",
    "DAS",
    "E",
    "COMERCIO",
    "SERVICOS",
}


@dataclass(frozen=True, slots=True)
class OpenTitle:
    id: uuid.UUID
    receivable: bool
    counterparty: str
    counterparty_document: str | None
    document_number: str | None
    due_date: date
    open_amount: Decimal


@dataclass(frozen=True, slots=True)
class BankEntry:
    id: uuid.UUID
    date: date
    amount: Decimal  # com sinal
    description: str


@dataclass(frozen=True, slots=True)
class MatchSuggestion:
    transaction_id: uuid.UUID
    title_ids: tuple[uuid.UUID, ...]
    allocated: tuple[Decimal, ...]
    score: Decimal
    reasons: tuple[str, ...]
    auto: bool = False


@dataclass(slots=True)
class MatchConfig:
    amount_tolerance: Decimal = Decimal("0.03")  # até 3% de diferença (taxas, juros, descontos)
    days_before: int = 5  # pagamento antes do vencimento
    days_after: int = 45  # atraso tolerado
    auto_threshold: Decimal = Decimal("0.85")
    min_score: Decimal = Decimal("0.45")
    max_group: int = 3
    reasons: list[str] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in normalize_text(text).replace(".", " ").replace("/", " ").split()
        if len(t) > 2 and t not in _STOP
    }


def _score_pair(
    entry: BankEntry, titles: Sequence[OpenTitle], cfg: MatchConfig
) -> tuple[Decimal, list[str]] | None:
    receivable = titles[0].receivable
    if (entry.amount > 0) != receivable:
        return None
    value = abs(entry.amount)
    total = sum((t.open_amount for t in titles), ZERO)
    diff = abs(value - total)
    reasons: list[str] = []
    if diff <= Decimal("0.01"):
        s_amount = Decimal("1")
        reasons.append("valor exato")
    elif total and diff / total <= cfg.amount_tolerance:
        s_amount = Decimal("0.6")
        reasons.append(f"valor com diferença de {(diff / total * 100):.1f}%")
    else:
        return None
    lag = min((entry.date - t.due_date).days for t in titles)
    if lag < -cfg.days_before or lag > cfg.days_after:
        return None
    s_date = Decimal("1") if -2 <= lag <= 3 else Decimal("0.7") if lag <= 15 else Decimal("0.4")
    reasons.append(
        "na data do vencimento"
        if -2 <= lag <= 3
        else f"{lag} dia(s) após o vencimento"
        if lag > 0
        else f"{-lag} dia(s) antes do vencimento"
    )
    desc = normalize_text(entry.description)
    desc_tokens = _tokens(entry.description)
    s_text = Decimal("0")
    for t in titles:
        if t.counterparty_document and t.counterparty_document in "".join(
            ch for ch in desc if ch.isdigit()
        ):
            s_text = Decimal("1")
            reasons.append("CNPJ/CPF no histórico")
            break
        if t.document_number and normalize_text(t.document_number) in desc:
            s_text = max(s_text, Decimal("0.9"))
            reasons.append(f"documento {t.document_number} no histórico")
        common = desc_tokens & _tokens(t.counterparty)
        if common:
            s_text = max(s_text, Decimal("0.8"))
            reasons.append("nome da contraparte no histórico")
    score = s_amount * Decimal("0.5") + s_date * Decimal("0.2") + s_text * Decimal("0.3")
    if len(titles) > 1:
        score -= Decimal("0.1")  # agrupamentos exigem mais evidência
    return score.quantize(Decimal("0.01")), reasons


def suggest_matches(
    entries: Sequence[BankEntry], titles: Sequence[OpenTitle], cfg: MatchConfig | None = None
) -> list[MatchSuggestion]:
    cfg = cfg or MatchConfig()
    candidates: list[MatchSuggestion] = []
    for e in entries:
        for t in titles:
            r = _score_pair(e, [t], cfg)
            if r and r[0] >= cfg.min_score:
                candidates.append(
                    MatchSuggestion(
                        e.id, (t.id,), (min(abs(e.amount), t.open_amount),), r[0], tuple(r[1])
                    )
                )
    used_e: set[uuid.UUID] = set()
    used_t: set[uuid.UUID] = set()
    chosen: list[MatchSuggestion] = []
    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        if c.transaction_id in used_e or c.title_ids[0] in used_t:
            continue
        chosen.append(_finalize(c, cfg))
        used_e.add(c.transaction_id)
        used_t.add(c.title_ids[0])
    # N:1 — um lançamento quitando várias duplicatas da mesma contraparte
    by_cp: dict[tuple[bool, str], list[OpenTitle]] = {}
    for t in titles:
        if t.id not in used_t:
            by_cp.setdefault((t.receivable, normalize_text(t.counterparty)), []).append(t)
    for e in entries:
        if e.id in used_e:
            continue
        best: MatchSuggestion | None = None
        for group in by_cp.values():
            free = [t for t in group if t.id not in used_t]
            for n in range(2, min(cfg.max_group, len(free)) + 1):
                for combo in itertools.combinations(free, n):
                    r = _score_pair(e, combo, cfg)
                    if r and r[0] >= cfg.min_score and (best is None or r[0] > best.score):
                        best = MatchSuggestion(
                            e.id,
                            tuple(t.id for t in combo),
                            tuple(t.open_amount for t in combo),
                            r[0],
                            (*r[1], f"{n} títulos agrupados"),
                        )
        if best:
            chosen.append(_finalize(best, cfg))
            used_e.add(e.id)
            used_t.update(best.title_ids)
    return sorted(chosen, key=lambda c: c.score, reverse=True)


def _finalize(s: MatchSuggestion, cfg: MatchConfig) -> MatchSuggestion:
    return MatchSuggestion(
        s.transaction_id,
        s.title_ids,
        s.allocated,
        s.score,
        tuple(dict.fromkeys(s.reasons)),
        s.score >= cfg.auto_threshold,
    )
