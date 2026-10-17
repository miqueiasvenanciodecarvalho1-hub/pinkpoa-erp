"""Enumerações do domínio. Persistidas como VARCHAR (portável entre SQLite/Postgres)."""

from __future__ import annotations

from enum import StrEnum


class PersonType(StrEnum):
    PF = "PF"  # Pessoa Física (CPF)
    PJ = "PJ"  # Pessoa Jurídica (CNPJ)


class TransactionType(StrEnum):
    INCOME = "INCOME"  # receita / crédito
    EXPENSE = "EXPENSE"  # despesa / débito


class ReconciliationStatus(StrEnum):
    PENDING = "PENDING"
    RECONCILED = "RECONCILED"
    IGNORED = "IGNORED"


class CategorizationSource(StrEnum):
    NONE = "NONE"
    RULE = "RULE"
    MANUAL = "MANUAL"


class SourceFormat(StrEnum):
    OFX = "OFX"
    CSV = "CSV"
    XLSX = "XLSX"
    CNAB240 = "CNAB240"
    CNAB400 = "CNAB400"
    MANUAL = "MANUAL"


class ImportStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CategoryGroup(StrEnum):
    """Agrupamento gerencial (base para DRE/DFC simplificados)."""

    REVENUE = "REVENUE"  # Receitas operacionais
    COGS = "COGS"  # Custo de mercadorias/serviços
    OPERATING_EXPENSE = "OPERATING_EXPENSE"
    PAYROLL = "PAYROLL"
    TAXES = "TAXES"
    FINANCIAL = "FINANCIAL"  # Tarifas, juros, IOF
    TRANSFER = "TRANSFER"  # Movimentação entre contas / aplicações (neutra no resultado)
    PERSONAL = "PERSONAL"  # Gastos pessoais (clientes PF)
    OTHER = "OTHER"


class MatchType(StrEnum):
    CONTAINS = "CONTAINS"  # palavra/expressão inteira contida no histórico normalizado
    STARTS_WITH = "STARTS_WITH"
    REGEX = "REGEX"


class TitleKind(StrEnum):
    RECEIVABLE = "RECEIVABLE"  # contas a receber
    PAYABLE = "PAYABLE"  # contas a pagar


class TitleStatus(StrEnum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class MatchMethod(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class StatementKind(StrEnum):
    BALANCE_SHEET = "BALANCE_SHEET"  # balanço patrimonial
    INCOME_STATEMENT = "INCOME_STATEMENT"  # DRE
    TRIAL_BALANCE = "TRIAL_BALANCE"  # balancete


class StatementStatus(StrEnum):
    DRAFT = "DRAFT"  # extraído, aguardando revisão humana
    VALIDATED = "VALIDATED"


class Role(StrEnum):
    MASTER = "MASTER"  # sócio/administrador da consultoria: tudo, inclusive usuários
    CONSULTANT = "CONSULTANT"  # opera clientes da organização
    CLIENT = "CLIENT"  # empresário: só o próprio cliente, leitura e envio de dados


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
