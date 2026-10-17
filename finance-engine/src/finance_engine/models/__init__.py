"""Importar este pacote registra todos os modelos no ``Base.metadata``."""

from finance_engine.models.banking import BankAccount, ImportBatch
from finance_engine.models.classification import CategorizationRule, Category, CostCenter
from finance_engine.models.enums import (
    CategorizationSource,
    CategoryGroup,
    ImportStatus,
    JobStatus,
    MatchMethod,
    MatchType,
    PersonType,
    ReconciliationStatus,
    Role,
    SourceFormat,
    StatementKind,
    StatementStatus,
    TitleKind,
    TitleStatus,
    TransactionType,
)
from finance_engine.models.security import Job, User
from finance_engine.models.statements import FinancialStatement
from finance_engine.models.tenancy import Client, Organization
from finance_engine.models.titles import FinancialTitle, Reconciliation
from finance_engine.models.transaction import Transaction

__all__ = [
    "BankAccount",
    "CategorizationRule",
    "CategorizationSource",
    "Category",
    "CategoryGroup",
    "Client",
    "CostCenter",
    "FinancialStatement",
    "FinancialTitle",
    "ImportBatch",
    "ImportStatus",
    "Job",
    "JobStatus",
    "MatchMethod",
    "MatchType",
    "Organization",
    "PersonType",
    "Reconciliation",
    "ReconciliationStatus",
    "Role",
    "SourceFormat",
    "StatementKind",
    "StatementStatus",
    "TitleKind",
    "TitleStatus",
    "Transaction",
    "TransactionType",
    "User",
]
