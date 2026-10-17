"""Usuários com papéis (RBAC) e fila de processamento persistente."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finance_engine.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from finance_engine.models.enums import JobStatus, Role


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=12))
    # Usuário CLIENT enxerga somente este cliente.
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE")
    )
    is_active: Mapped[bool] = mapped_column(default=True)


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tarefa em segundo plano. O worker pega a mais antiga QUEUED cujo ``run_after`` já passou."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]]
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=10), default=JobStatus.QUEUED
    )
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    run_after: Mapped[dt.datetime]
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None]
