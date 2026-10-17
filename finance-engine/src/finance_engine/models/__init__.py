"""Importar este pacote registra todos os modelos no ``Base.metadata``."""

from finance_engine.models.banking import BankAccount, ImportBatch
from finance_engine.models.classification import CategorizationRule, Category, CostCenter
from finance_engine.models.enums import (
    CategorizationSource,
    CategoryGroup,
    ImportStatus,
    MatchType,
    PersonType,
    ReconciliationStatus,
    SourceFormat,
    TransactionType,
)
from finance_engine.models.tenancy import Client, Organization
from finance_engine.models.transaction import Transaction

__all__ = [
    "BankAccount",
    "CategorizationRule",
    "CategorizationSource",
    "Category",
    "CategoryGroup",
    "Client",
    "CostCenter",
    "ImportBatch",
    "ImportStatus",
    "MatchType",
    "Organization",
    "PersonType",
    "ReconciliationStatus",
    "SourceFormat",
    "Transaction",
    "TransactionType",
]
