"""Pipeline de ingestão: detectar → parsear → deduplicar → categorizar → persistir.

O fluxo é dividido em duas etapas para suportar processamento síncrono e assíncrono:

* ``register`` (rápida, dentro do request): valida tenant/conta, detecta o formato, calcula o
  hash do arquivo e cria o ``ImportBatch`` em ``PENDING`` — rejeitando reenvio do mesmo arquivo.
* ``process`` (pesada): roda o parser e grava os lançamentos numa única transação. Pode rodar no
  request, num ``BackgroundTask`` ou num worker (Celery/RQ/ARQ) — só depende do ``batch_id``.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_engine.core.exceptions import (
    DuplicateImportError,
    FinanceEngineError,
    NotFoundError,
    ParseError,
)
from finance_engine.db.base import utcnow
from finance_engine.ingestion import ParserOptions, RawTransaction, detect_format, get_parser
from finance_engine.ingestion.base import ParseResult
from finance_engine.ingestion.normalization import normalize_text, only_digits
from finance_engine.models import (
    BankAccount,
    ImportBatch,
    ImportStatus,
    SourceFormat,
    Transaction,
    TransactionType,
)
from finance_engine.services.categorization_service import apply_categorization, load_engine

logger = logging.getLogger(__name__)

MAX_WARNINGS_STORED = 200


@dataclass(slots=True)
class ImportRequest:
    organization_id: uuid.UUID
    client_id: uuid.UUID
    bank_account_id: uuid.UUID
    filename: str
    content: bytes
    source_format: SourceFormat | None = None  # None = autodetectar
    options: ParserOptions = field(default_factory=ParserOptions)


def compute_fingerprints(transactions: Sequence[RawTransaction]) -> list[str]:
    """Chave de deduplicação estável entre importações.

    Usa o identificador do banco (FITID / nosso número) quando existe. Sem ele, combina
    data+valor+histórico+documento e um **índice de ocorrência**, para que dois lançamentos
    legítimos idênticos no mesmo dia (ex.: duas tarifas PIX de R$ 0,50) não sejam colapsados,
    enquanto o mesmo dia reimportado num extrato sobreposto continue sendo reconhecido.
    """
    seen: Counter[str] = Counter()
    fingerprints: list[str] = []
    for tx in transactions:
        if tx.external_id:
            base = f"ext|{tx.external_id}|{tx.date.isoformat()}|{tx.amount}"
        else:
            base = "|".join(
                (
                    tx.date.isoformat(),
                    str(tx.amount),
                    normalize_text(tx.description),
                    normalize_text(tx.document or ""),
                )
            )
        occurrence = seen[base]
        seen[base] += 1
        fingerprints.append(hashlib.sha256(f"{base}|{occurrence}".encode()).hexdigest())
    return fingerprints


class IngestionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ etapa 1

    def register(self, request: ImportRequest) -> ImportBatch:
        account = self.session.get(BankAccount, request.bank_account_id)
        if (
            account is None
            or account.organization_id != request.organization_id
            or account.client_id != request.client_id
        ):
            raise NotFoundError("conta bancária não encontrada para este cliente")

        source_format = request.source_format or detect_format(request.filename, request.content)
        sha256 = hashlib.sha256(request.content).hexdigest()

        existing = self.session.scalar(
            select(ImportBatch).where(
                ImportBatch.bank_account_id == account.id, ImportBatch.file_sha256 == sha256
            )
        )
        if existing is not None:
            if existing.status is not ImportStatus.FAILED:
                raise DuplicateImportError(
                    f"arquivo já importado em {existing.created_at:%d/%m/%Y %H:%M}",
                    existing_batch_id=str(existing.id),
                )
            # Reprocessamento de uma importação que falhou: reaproveita o lote.
            existing.status = ImportStatus.PENDING
            existing.source_format = source_format
            existing.filename = request.filename
            existing.error_message = None
            existing.warnings = []
            self.session.flush()
            return existing

        batch = ImportBatch(
            organization_id=request.organization_id,
            client_id=request.client_id,
            bank_account_id=account.id,
            source_format=source_format,
            filename=request.filename[:255],
            file_sha256=sha256,
            status=ImportStatus.PENDING,
            warnings=[],
        )
        self.session.add(batch)
        self.session.flush()
        return batch

    # ------------------------------------------------------------------ etapa 2

    def process(
        self,
        batch_id: uuid.UUID,
        content: bytes,
        options: ParserOptions | None = None,
        *,
        raise_on_error: bool = True,
    ) -> ImportBatch:
        """Processa o lote. Em falha, o lote fica ``FAILED`` com a mensagem de erro gravada."""
        batch = self._get_batch(batch_id)
        batch.status = ImportStatus.PROCESSING
        batch.started_at = utcnow()
        self.session.commit()  # status visível para quem consulta o lote durante o processamento

        try:
            parser = get_parser(batch.source_format, options)
            result = parser.parse(content)
            self._persist(batch, result)
            self.session.commit()
        except (FinanceEngineError, IntegrityError) as exc:
            self.session.rollback()
            message = (
                "importação concorrente da mesma conta; tente novamente"
                if isinstance(exc, IntegrityError)
                else str(exc)
            )
            self._mark_failed(batch_id, message)
            if raise_on_error:
                error = exc if isinstance(exc, FinanceEngineError) else ParseError(message)
                error.batch_id = str(batch_id)
                if error is exc:
                    raise
                raise error from exc
        except Exception:
            self.session.rollback()
            logger.exception("falha inesperada processando lote %s", batch_id)
            self._mark_failed(batch_id, "erro interno ao processar o arquivo")
            raise
        return self._get_batch(batch_id)

    def import_file(self, request: ImportRequest) -> ImportBatch:
        """Atalho síncrono: registra e processa no mesmo fluxo."""
        batch = self.register(request)
        self.session.commit()
        return self.process(batch.id, request.content, request.options)

    # ------------------------------------------------------------------ internos

    def _persist(self, batch: ImportBatch, result: ParseResult) -> None:
        account = self.session.get(BankAccount, batch.bank_account_id)
        assert account is not None
        warnings = list(result.warnings)
        self._check_account_matches(account, result, warnings)

        fingerprints = compute_fingerprints(result.transactions)
        dates = [t.date for t in result.transactions]
        existing = set(
            self.session.scalars(
                select(Transaction.fingerprint).where(
                    Transaction.bank_account_id == account.id,
                    Transaction.date.between(min(dates), max(dates)),
                )
            )
        )

        new_transactions: list[Transaction] = []
        duplicates = 0
        for raw, fingerprint in zip(result.transactions, fingerprints, strict=True):
            if fingerprint in existing:
                duplicates += 1
                continue
            existing.add(fingerprint)
            new_transactions.append(
                Transaction(
                    organization_id=batch.organization_id,
                    client_id=batch.client_id,
                    bank_account_id=account.id,
                    import_batch_id=batch.id,
                    date=raw.date,
                    description=raw.description[:500],
                    normalized_description=normalize_text(raw.description)[:500],
                    amount=raw.amount,
                    type=TransactionType.INCOME if raw.amount > 0 else TransactionType.EXPENSE,
                    document=raw.document,
                    external_id=raw.external_id,
                    fingerprint=fingerprint,
                    source_format=batch.source_format,
                )
            )

        engine = load_engine(self.session, batch.organization_id, batch.client_id)
        categorized = apply_categorization(engine, new_transactions)
        self.session.add_all(new_transactions)
        self._maybe_set_opening_balance(account, result)

        batch.total_rows = result.total_rows
        batch.imported_count = len(new_transactions)
        batch.duplicate_count = duplicates
        batch.skipped_count = result.skipped_rows
        batch.categorized_count = categorized
        batch.warnings = warnings[:MAX_WARNINGS_STORED]
        batch.status = ImportStatus.COMPLETED
        batch.finished_at = utcnow()
        self.session.flush()

    @staticmethod
    def _check_account_matches(
        account: BankAccount, result: ParseResult, warnings: list[str]
    ) -> None:
        meta = result.metadata
        if meta.bank_code and only_digits(meta.bank_code).zfill(3) != account.bank_code.zfill(3):
            warnings.append(
                f"Banco do arquivo ({meta.bank_code}) difere do cadastro ({account.bank_code})"
            )
        file_account = only_digits(meta.account_number or "").lstrip("0")
        registered = only_digits(account.account_number).lstrip("0")
        unrelated = registered not in file_account and file_account not in registered
        if file_account and registered and unrelated:
            warnings.append(
                f"Conta do arquivo ({meta.account_number}) difere do cadastro "
                f"({account.account_number}) — confirme se o extrato é desta conta"
            )

    @staticmethod
    def _maybe_set_opening_balance(account: BankAccount, result: ParseResult) -> None:
        """Deriva o saldo inicial do saldo final informado pelo banco (OFX LEDGERBAL).

        saldo_inicial = saldo_final - Σ lançamentos do arquivo até a data do saldo.
        Só é aplicado se a conta ainda não tem saldo inicial definido.
        """
        meta = result.metadata
        if account.opening_balance_date is not None or meta.balance is None:
            return
        cutoff = meta.balance_date or meta.period_end
        included = [t for t in result.transactions if cutoff is None or t.date <= cutoff]
        if not included:
            return
        account.opening_balance = meta.balance - sum((t.amount for t in included), Decimal("0"))
        account.opening_balance_date = min(t.date for t in included)

    def _mark_failed(self, batch_id: uuid.UUID, message: str) -> None:
        batch = self._get_batch(batch_id)
        batch.status = ImportStatus.FAILED
        batch.error_message = message[:2000]
        batch.finished_at = utcnow()
        self.session.commit()

    def _get_batch(self, batch_id: uuid.UUID) -> ImportBatch:
        batch = self.session.get(ImportBatch, batch_id)
        if batch is None:
            raise NotFoundError(f"lote de importação {batch_id} não encontrado")
        return batch
