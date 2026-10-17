"""Fila de processamento persistente (tabela ``jobs``) com retentativas e worker dedicado.

Uso: ``enqueue(session, "import_file", {...})``.
Em produção rode ``python -m finance_engine.worker``
(um ou mais processos); no PostgreSQL a captura usa ``FOR UPDATE SKIP LOCKED``, então vários
workers não pegam o mesmo job. Em desenvolvimento (``FIN_JOBS_INLINE=true``) a API executa o job
logo após responder.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finance_engine.db.base import utcnow
from finance_engine.models import Job, JobStatus

logger = logging.getLogger(__name__)
Handler = Callable[[Session, dict[str, Any]], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}


def job_handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


def enqueue(
    session: Session, kind: str, payload: dict[str, Any], organization_id: uuid.UUID | None = None
) -> Job:
    job = Job(
        kind=kind,
        payload=payload,
        organization_id=organization_id,
        run_after=utcnow(),
        status=JobStatus.QUEUED,
    )
    session.add(job)
    session.flush()
    return job


def run_job(factory: sessionmaker[Session], job_id: uuid.UUID) -> JobStatus:
    with factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return job.status if job else JobStatus.FAILED
        job.status, job.attempts = JobStatus.RUNNING, job.attempts + 1
        session.commit()
        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError(f"sem handler para {job.kind}")
            job.result = handler(session, dict(job.payload)) or {}
            job.status, job.error = JobStatus.DONE, None
            session.commit()
        except Exception as exc:
            session.rollback()
            job = session.get(Job, job_id)
            assert job is not None
            job.error = str(exc)[:2000]
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
            else:
                job.status = JobStatus.QUEUED
                job.run_after = utcnow() + timedelta(minutes=2**job.attempts)
            session.commit()
            logger.warning("job %s (%s) falhou: %s", job_id, job.kind, exc)
        return job.status


def claim_next(session: Session) -> uuid.UUID | None:
    job = session.scalar(
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= utcnow())
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return job.id if job else None


def run_worker(
    factory: sessionmaker[Session], *, once: bool = False, poll_seconds: float = 2.0
) -> int:
    processed = 0
    while True:
        with factory() as session:
            job_id = claim_next(session)
            session.commit()
        if job_id is None:
            if once:
                return processed
            time.sleep(poll_seconds)
            continue
        run_job(factory, job_id)
        processed += 1
