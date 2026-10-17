"""Serviço de consultas cadastrais com validação, cache em memória e fallback entre provedores."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

import httpx

from finance_engine.core.documents import is_valid_cnpj
from finance_engine.core.exceptions import FinanceEngineError, NotFoundError
from finance_engine.lookup import providers as prov
from finance_engine.lookup.enrichment import enrich
from finance_engine.lookup.models import Address, Bank, CompanyRecord, Holiday

logger = logging.getLogger(__name__)
T = TypeVar("T")


class InvalidDocumentError(FinanceEngineError):
    pass


class LookupUnavailableError(FinanceEngineError):
    """Todos os provedores falharam (fora do ar ou limite de requisições)."""


class TTLCache(Generic[T]):
    """LRU com expiração. Em produção, troque por Redis ou tabela (mesma interface)."""

    def __init__(self, ttl_seconds: float, max_items: int = 5000) -> None:
        self.ttl, self.max = ttl_seconds, max_items
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> T | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or item[0] < time.monotonic():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return item[1]

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max:
                self._data.popitem(last=False)


def only_digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


class LookupService:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        cnpj_providers: Sequence[prov.CnpjProvider] = prov.CNPJ_PROVIDERS,
        cep_providers: Sequence[prov.CepProvider] = prov.CEP_PROVIDERS,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={"User-Agent": "finance-engine/0.1 (consultas cadastrais)"},
            follow_redirects=True,
        )
        self.cnpj_providers = cnpj_providers
        self.cep_providers = cep_providers
        self._cnpj_cache: TTLCache[CompanyRecord] = TTLCache(24 * 3600)
        self._cep_cache: TTLCache[Address] = TTLCache(30 * 24 * 3600)
        self._banks_cache: TTLCache[list[Bank]] = TTLCache(7 * 24 * 3600, 1)
        self._holidays_cache: TTLCache[list[Holiday]] = TTLCache(30 * 24 * 3600, 50)

    def company(self, cnpj: str) -> CompanyRecord:
        digits = only_digits(cnpj)
        if not is_valid_cnpj(digits):
            raise InvalidDocumentError("CNPJ inválido (dígitos verificadores não conferem)")
        cached = self._cnpj_cache.get(digits)
        if cached is not None:
            return cached
        record = enrich(self._first(self.cnpj_providers, digits, "CNPJ"))
        self._cnpj_cache.set(digits, record)
        return record

    def address(self, cep: str) -> Address:
        digits = only_digits(cep)
        if len(digits) != 8:
            raise InvalidDocumentError("CEP deve ter 8 dígitos")
        cached = self._cep_cache.get(digits)
        if cached is not None:
            return cached
        found = self._first(self.cep_providers, digits, "CEP")
        self._cep_cache.set(digits, found)
        return found

    def banks(self) -> list[Bank]:
        cached = self._banks_cache.get("all")
        if cached is None:
            cached = self._call(lambda: prov.banks_brasilapi(self.client), "bancos")
            self._banks_cache.set("all", cached)
        return cached

    def holidays(self, year: int) -> list[Holiday]:
        if not 1900 <= year <= 2199:
            raise InvalidDocumentError("ano fora do intervalo")
        cached = self._holidays_cache.get(str(year))
        if cached is None:
            cached = self._call(lambda: prov.holidays_brasilapi(self.client, year), "feriados")
            self._holidays_cache.set(str(year), cached)
        return cached

    # ------------------------------------------------------------------ internos

    def _first(
        self, providers: Sequence[Callable[[httpx.Client, str], T]], key: str, label: str
    ) -> T:
        not_found = 0
        for provider in providers:
            try:
                return provider(self.client, key)
            except prov.NotFound:
                not_found += 1
            except prov.ProviderError as exc:
                logger.warning("consulta de %s falhou em %s: %s", label, provider.__name__, exc)
        if not_found and not_found == len(providers):
            raise NotFoundError(f"{label} {key} não encontrado")
        if not_found:
            raise NotFoundError(f"{label} {key} não encontrado nos serviços disponíveis")
        raise LookupUnavailableError(
            f"serviços de consulta de {label} indisponíveis; tente em instantes"
        )

    @staticmethod
    def _call(fn: Callable[[], T], label: str) -> T:
        try:
            return fn()
        except prov.NotFound as exc:
            raise NotFoundError(f"{label} não encontrado") from exc
        except prov.ProviderError as exc:
            raise LookupUnavailableError(f"serviço de {label} indisponível") from exc


_default: LookupService | None = None


def get_lookup_service() -> LookupService:
    global _default
    if _default is None:
        _default = LookupService()
    return _default
