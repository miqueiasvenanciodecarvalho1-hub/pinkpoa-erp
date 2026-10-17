"""Contratos HTTP (Pydantic v2). Separados dos modelos ORM de propósito."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finance_engine.analysis.cashflow import AnomalyKind, Severity
from finance_engine.analysis.forecast import Cadence, Scenario
from finance_engine.analysis.statements import Granularity
from finance_engine.core.documents import is_valid_cnpj, is_valid_cpf
from finance_engine.ingestion.tabular import ColumnMapping
from finance_engine.models import (
    CategorizationSource,
    CategoryGroup,
    ImportStatus,
    MatchType,
    PersonType,
    ReconciliationStatus,
    SourceFormat,
    TransactionType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------------------- tenancy


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,80}$")
    seed_default_taxonomy: bool = True
    admin_email: str | None = Field(default=None, description="Cria o primeiro usuário Master")
    admin_name: str | None = None
    admin_password: str | None = Field(default=None, min_length=8)


class OrganizationRead(ORMModel):
    id: uuid.UUID
    name: str
    slug: str


class ClientCreate(BaseModel):
    person_type: PersonType
    name: str = Field(min_length=2, max_length=200)
    document: str = Field(description="CPF (PF) ou CNPJ (PJ), com ou sem máscara")

    @model_validator(mode="after")
    def _validate_document(self) -> Self:
        digits = "".join(ch for ch in self.document if ch.isdigit())
        valid = is_valid_cpf(digits) if self.person_type is PersonType.PF else is_valid_cnpj(digits)
        if not valid:
            kind = "CPF" if self.person_type is PersonType.PF else "CNPJ"
            raise ValueError(f"{kind} inválido")
        self.document = digits
        return self


class ClientRead(ORMModel):
    id: uuid.UUID
    person_type: PersonType
    name: str
    document: str
    trade_name: str | None = None
    registry_data: dict[str, Any] | None = None


class ClientFromCnpj(BaseModel):
    cnpj: str = Field(description="Só o CNPJ: nome, endereço, CNAE e sócios vêm da Receita")


# ----------------------------------------------------------------------------- consultas


class AddressOut(ORMModel):
    cep: str
    street: str
    number: str
    complement: str
    district: str
    city: str
    state: str
    ibge_code: str | None
    source: str


class ActivityOut(ORMModel):
    code: str
    description: str


class PartnerOut(ORMModel):
    name: str
    document: str
    kind: str
    role: str
    since: date | None


class CompanyRecordOut(ORMModel):
    cnpj: str
    legal_name: str
    trade_name: str
    status: str
    status_date: date | None
    opened_at: date | None
    legal_nature: str
    size: str
    share_capital: Decimal
    simples: bool | None
    mei: bool | None
    main_activity: ActivityOut | None
    secondary_activities: list[ActivityOut]
    address: AddressOut | None
    phone: str
    email: str
    head_office: bool
    partners: list[PartnerOut]
    source: str
    fetched_at: datetime
    alerts: list[str]
    suggested_sector: str | None
    suggested_specialties: list[str]


class BankOut(ORMModel):
    code: str
    ispb: str
    name: str
    full_name: str


class HolidayOut(ORMModel):
    date: date
    name: str
    kind: str


class BankAccountCreate(BaseModel):
    bank_code: str = Field(pattern=r"^\d{3}$")
    branch: str = Field(min_length=1, max_length=10)
    account_number: str = Field(min_length=1, max_length=20)
    nickname: str | None = None
    opening_balance: Decimal = Decimal("0")
    opening_balance_date: date | None = None


class BankAccountRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    bank_code: str
    branch: str
    account_number: str
    nickname: str | None
    opening_balance: Decimal
    opening_balance_date: date | None


# ----------------------------------------------------------------------------- imports


class ColumnMappingIn(BaseModel):
    """Mapeamento opcional de colunas para CSV/XLSX (nomes exatamente como no cabeçalho)."""

    model_config = ConfigDict(extra="forbid")

    date: str | None = None
    description: list[str] | str | None = None
    amount: str | None = None
    credit: str | None = None
    debit: str | None = None
    document: str | None = None
    dc_indicator: str | None = None

    def to_domain(self) -> ColumnMapping:
        description = (
            [self.description] if isinstance(self.description, str) else (self.description or [])
        )
        return ColumnMapping(
            date=self.date,
            description=description,
            amount=self.amount,
            credit=self.credit,
            debit=self.debit,
            document=self.document,
            dc_indicator=self.dc_indicator,
        )


class ImportBatchRead(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    bank_account_id: uuid.UUID
    source_format: SourceFormat
    filename: str
    status: ImportStatus
    total_rows: int
    imported_count: int
    duplicate_count: int
    skipped_count: int
    categorized_count: int
    warnings: list[str]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ----------------------------------------------------------------------------- classificação


class CategoryRead(ORMModel):
    id: uuid.UUID
    name: str
    group: CategoryGroup


class RuleCreate(BaseModel):
    pattern: str = Field(min_length=2, max_length=200)
    category_id: uuid.UUID
    match_type: MatchType = MatchType.CONTAINS
    applies_to: TransactionType | None = None
    client_id: uuid.UUID | None = Field(
        default=None, description="Preencha para criar uma regra exclusiva de um cliente"
    )
    priority: int = Field(default=150, ge=0, le=1000)
    apply_now: bool = Field(default=True, description="Recategoriza lançamentos sem categoria")


class RuleRead(ORMModel):
    id: uuid.UUID
    pattern: str
    category_id: uuid.UUID
    match_type: MatchType
    applies_to: TransactionType | None
    client_id: uuid.UUID | None
    priority: int


class RecategorizeResult(BaseModel):
    categorized: int


# ----------------------------------------------------------------------------- lançamentos


class TransactionRead(ORMModel):
    id: uuid.UUID
    bank_account_id: uuid.UUID
    date: date
    description: str
    amount: Decimal
    type: TransactionType
    document: str | None
    category_id: uuid.UUID | None
    cost_center_id: uuid.UUID | None
    categorization_source: CategorizationSource
    reconciliation_status: ReconciliationStatus
    source_format: SourceFormat


class TransactionPage(BaseModel):
    items: list[TransactionRead]
    total: int
    limit: int
    offset: int


class TransactionCategoryUpdate(BaseModel):
    category_id: uuid.UUID | None
    cost_center_id: uuid.UUID | None = None


# ----------------------------------------------------------------------------- análises


class DailyCashFlowOut(ORMModel):
    date: date
    inflow: Decimal
    outflow: Decimal
    net: Decimal
    opening_balance: Decimal
    closing_balance: Decimal
    is_projected: bool


class CategoryTotalOut(ORMModel):
    category: str
    total: Decimal
    share: Decimal
    count: int


class AnomalyOut(ORMModel):
    kind: AnomalyKind
    severity: Severity
    start_date: date | None
    end_date: date | None
    value: Decimal
    message: str


class SummaryOut(ORMModel):
    opening_balance: Decimal
    closing_balance: Decimal
    total_inflow: Decimal
    total_outflow: Decimal
    net: Decimal
    min_balance: Decimal
    min_balance_date: date | None
    avg_daily_outflow: Decimal
    runway_days: Decimal | None


class RecurringPatternOut(ORMModel):
    signature: str
    description: str
    category: str | None
    group: str | None
    cadence: Cadence
    amount: Decimal
    occurrences: int
    last_date: date
    confidence: Decimal


class CashFlowReportOut(ORMModel):
    client_id: uuid.UUID
    start: date
    end: date
    today: date
    scenario: Scenario | None
    bank_account_ids: list[uuid.UUID]
    summary: SummaryOut
    daily: list[DailyCashFlowOut]
    top_expenses: list[CategoryTotalOut]
    anomalies: list[AnomalyOut]
    recurring_patterns: list[RecurringPatternOut]


class StatementPeriodOut(ORMModel):
    period_start: date
    period_end: date
    opening_balance: Decimal
    inflow_by_group: dict[str, Decimal]
    outflow_by_group: dict[str, Decimal]
    operating_cash_flow: Decimal
    financial_cash_flow: Decimal
    transfers: Decimal
    unclassified: Decimal
    net: Decimal
    closing_balance: Decimal
    forecast_share: Decimal


class CashFlowStatementOut(ORMModel):
    client_id: uuid.UUID
    granularity: Granularity
    scenario: Scenario | None
    periods: list[StatementPeriodOut]


# ----------------------------------------------------------------------------- autenticação


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    client_id: uuid.UUID | None = None


class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=2)
    password: str = Field(min_length=8)
    role: str = Field(pattern=r"^(MASTER|CONSULTANT|CLIENT)$")
    client_id: uuid.UUID | None = None


class UserRead(ORMModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    client_id: uuid.UUID | None


# ------------------------------------------------------------------- títulos e conciliação


class TitleRead(ORMModel):
    id: uuid.UUID
    kind: str
    counterparty: str
    counterparty_document: str | None
    document_number: str | None
    issue_date: date | None
    due_date: date
    amount: Decimal
    open_amount: Decimal
    status: str
    paid_date: date | None


class TitleImportOut(BaseModel):
    created: int
    updated: int
    errors: list[str]


class AgingBucketOut(ORMModel):
    label: str
    overdue: bool
    total: Decimal
    count: int
    share: Decimal


class ExposureOut(ORMModel):
    counterparty: str
    total: Decimal
    overdue: Decimal
    share: Decimal
    oldest_overdue_days: int


class AgingOut(ORMModel):
    reference_date: date
    total_open: Decimal
    total_overdue: Decimal
    overdue_ratio: Decimal
    weighted_days_to_due: Decimal
    buckets: list[AgingBucketOut]
    top_counterparties: list[ExposureOut]
    concentration_top5: Decimal


class MatchOut(ORMModel):
    transaction_id: uuid.UUID
    title_ids: list[uuid.UUID]
    allocated: list[Decimal]
    score: Decimal
    reasons: list[str]
    auto: bool


class ApplyMatchesIn(BaseModel):
    matches: list[MatchOut] | None = Field(
        default=None, description="Vazio = aplicar só as sugestões automáticas"
    )


class ApplyMatchesOut(BaseModel):
    applied: int


# ----------------------------------------------------------------------------- antecipação


class ReceivableIn(BaseModel):
    amount: Decimal = Field(gt=0)
    due_date: date
    reference: str = ""


class AnticipationIn(BaseModel):
    monthly_rate: Decimal = Field(ge=0, le=1, description="0.025 = 2,5% a.m.")
    operation_date: date | None = None
    float_days: int = Field(default=1, ge=0, le=10)
    fee_per_title: Decimal = Decimal("0")
    fixed_fee: Decimal = Decimal("0")
    iof: bool = True
    simples_nacional: bool = False
    alternative_monthly_rate: Decimal | None = None
    receivables: list[ReceivableIn] | None = None
    title_ids: list[uuid.UUID] | None = Field(
        default=None, description="Títulos a receber do cliente"
    )


class AnticipationTitleOut(ORMModel):
    reference: str
    amount: Decimal
    due_date: date
    days: int
    discount: Decimal
    iof: Decimal
    fee: Decimal
    net: Decimal


class AnticipationOut(ORMModel):
    titles: list[AnticipationTitleOut]
    face_value: Decimal
    discount: Decimal
    iof: Decimal
    fees: Decimal
    net_value: Decimal
    total_cost: Decimal
    cost_pct_of_face: Decimal
    weighted_days: Decimal
    effective_monthly_rate: Decimal
    effective_annual_rate: Decimal
    alternative_cost: Decimal | None
    recommendation: str
    warnings: list[str]


# ------------------------------------------------------------- demonstrativos e indicadores


class StatementRead(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    period_start: date | None
    period_end: date | None
    source: str
    filename: str
    values: dict[str, Decimal]
    lines: list[str]
    warnings: list[str]


class StatementUpdate(BaseModel):
    values: dict[str, Decimal] = Field(default_factory=dict)
    validate_statement: bool = Field(default=False, alias="validate")


class IndicatorsOut(ORMModel):
    pmr: Decimal | None
    pme: Decimal | None
    pmp: Decimal | None
    operating_cycle: Decimal | None
    cash_cycle: Decimal | None
    ncg: Decimal | None
    working_capital: Decimal | None
    treasury_balance: Decimal | None
    current_ratio: Decimal | None
    quick_ratio: Decimal | None
    cash_ratio: Decimal | None
    debt_to_equity: Decimal | None
    gross_margin: Decimal | None
    net_margin: Decimal | None
    contribution_margin: Decimal | None
    fixed_costs: Decimal | None
    break_even_revenue: Decimal | None
    safety_margin: Decimal | None
    alerts: list[str]
    missing: list[str]


class IndicatorsReportOut(BaseModel):
    accounting: IndicatorsOut
    operational_pmr: Decimal | None
    operational_pmp: Decimal | None
    statements: list[StatementRead]
