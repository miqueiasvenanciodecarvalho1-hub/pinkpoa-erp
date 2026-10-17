"""Configuração da aplicação via variáveis de ambiente (prefixo ``FIN_``)."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIN_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./finance_engine.db"
    sql_echo: bool = False
    # Em produção o schema é gerido por migrações (Alembic); create_all é só para dev/testes.
    auto_create_schema: bool = True

    max_upload_bytes: int = 20 * 1024 * 1024
    # Fração máxima de linhas inválidas tolerada antes de rejeitar o arquivo inteiro.
    max_invalid_row_ratio: float = 0.2

    low_balance_threshold: Decimal = Decimal("0")
    outflow_spike_mad_factor: Decimal = Decimal("5")
    category_concentration_ratio: Decimal = Decimal("0.4")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
