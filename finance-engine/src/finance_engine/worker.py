"""Worker da fila: ``python -m finance_engine.worker`` (deixe rodando ao lado da API)."""

from __future__ import annotations

import logging

import finance_engine.services.job_handlers  # noqa: F401 - registra os handlers
from finance_engine.db.session import get_session_factory
from finance_engine.services.jobs import run_worker

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker(get_session_factory())
