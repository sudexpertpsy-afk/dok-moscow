"""Модели данных Док.Москва (Приложение А ТЗ, пакеты W-01/W-02, W-10, W-16)."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    service_admin = "service_admin"
    user = "user"


class OrgRole(str, enum.Enum):
    """Роль внутри организации (W-27). Не путать с UserRole.service_admin."""

    org_admin = "org_admin"
    org_member = "org_member"


class OrgFieldType(str, enum.Enum):
    """Типы пользовательских полей организации (W-41)."""

    string = "string"
    multiline = "multiline"
    date = "date"
    money = "money"
    checkbox = "checkbox"
    select = "select"
    counter = "counter"


class CounterpartyType(str, enum.Enum):
    fl = "fl"  # физлицо
    ul = "ul"  # юрлицо
    expert = "expert"


class CounterpartySource(str, enum.Enum):
    manual = "manual"
    dadata = "dadata"


class DocumentFormat(str, enum.Enum):
    docx = "docx"
    pdf = "pdf"


class TariffCode(str, enum.Enum):
    guest = "guest"
    specialist = "specialist"
    organization = "organization"


class SubscriptionPeriod(str, enum.Enum):
    month = "month"
    year = "year"


class SubscriptionStatus(str, enum.Enum):
    trial = "trial"
    active = "active"
    expired = "expired"
    cancelled = "cancelled"


class PaymentStatus(str, enum.Enum):
    created = "created"
    authorized = "authorized"
    confirmed = "confirmed"
    rejected = "rejected"
    refunded = "refunded"
    partial_refund = "partial_refund"


class PaymentSource(str, enum.Enum):
    card = "card"
    manual = "manual"


class PaymentMode(str, enum.Enum):
    test = "test"
    live = "live"


class PromoCodeType(str, enum.Enum):
    percent = "percent"
    fixed = "fixed"


class CalendarEventKind(str, enum.Enum):
    plan = "plan"
    meeting = "meeting"
    contract_end = "contract_end"
    contract_start = "contract_start"
    payment_due = "payment_due"
    other = "other"


class CalendarEventStatus(str, enum.Enum):
    planned = "planned"
    done = "done"
    cancelled = "cancelled"


class LegalActCategory(str, enum.Enum):
    law = "law"  # профильный закон
    code = "code"  # процессуальный / отраслевой кодекс
    plenum = "plenum"  # разъяснения высших судов
    order = "order"  # ведомственный приказ
    standard = "standard"  # ГОСТ (карточка)


class LegalActStatus(str, enum.Enum):
    active = "active"
    repealed = "repealed"


class LegalActMode(str, enum.Enum):
    full_text = "full_text"
    fragments = "fragments"
    card = "card"


class ActVersionStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ActWatchResult(str, enum.Enum):
    unchanged = "unchanged"
    change_found = "change_found"
    source_error = "source_error"


JsonType = JSON().with_variant(JSONB(), "postgresql")

_STR_ENUM = dict(
    values_callable=lambda enum: [item.value for item in enum],
    native_enum=False,
)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # JSONB: организация/банк/подписанты/прайс/склонения (как настройки.yaml)
    requisites: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # W-39: заявка, из которой родилась организация
    source_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL", use_alter=True, name="fk_orgs_source_lead"),
        nullable=True,
        index=True,
    )

    users: Mapped[list[User]] = relationship(back_populates="organization")
    invites: Mapped[list[Invite]] = relationship(back_populates="organization")
    counterparties: Mapped[list[Counterparty]] = relationship(back_populates="organization")
    contracts: Mapped[list[Contract]] = relationship(back_populates="organization")
    documents: Mapped[list[Document]] = relationship(back_populates="organization")
    counters: Mapped[list[Counter]] = relationship(back_populates="organization")
    org_fields: Mapped[list["OrgField"]] = relationship(back_populates="organization")
    events: Mapped[list[Event]] = relationship(back_populates="organization")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="organization")
    payments: Mapped[list["Payment"]] = relationship(back_populates="organization")
    calendar_events: Mapped[list["CalendarEvent"]] = relationship(back_populates="organization")
    party_checks: Mapped[list["PartyCheck"]] = relationship(back_populates="organization")
    source_lead: Mapped["Lead | None"] = relationship(
        foreign_keys=[source_lead_id],
        post_update=True,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    # NULL — вход только через OAuth (W-25)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", **_STR_ENUM),
        nullable=False,
        default=UserRole.user,
    )
    # W-27: роль в организации; NULL у service_admin без org
    org_role: Mapped[OrgRole | None] = mapped_column(
        Enum(OrgRole, name="org_role", **_STR_ENUM),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # W-24: TOTP 2FA (секрет — Fernet от SECRET_KEY; резервные коды — только хэши)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    backup_codes_hashes: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    # Порядок пунктов бокового меню: {"cabinet": ["calendar", ...], "admin": [...]}
    nav_order: Mapped[dict | None] = mapped_column(JsonType, nullable=True)

    organization: Mapped[Organization | None] = relationship(back_populates="users")
    documents_created: Mapped[list[Document]] = relationship(back_populates="created_by_user")
    events: Mapped[list[Event]] = relationship(back_populates="user")
    oauth_identities: Mapped[list[OAuthIdentity]] = relationship(back_populates="user")


class OAuthIdentity(Base):
    """Внешняя OAuth-привязка (W-25: Яндекс ID)."""

    __tablename__ = "oauth_identities"
    __table_args__ = (
        Index("uq_oauth_identities_provider_sub", "provider", "sub", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    sub: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="oauth_identities")


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # W-39: связь с заявкой лендинга (для авто-статуса «зарегистрирован»)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL", use_alter=True, name="fk_invites_lead"),
        nullable=True,
        index=True,
    )

    organization: Mapped[Organization] = relationship(back_populates="invites")
    lead: Mapped["Lead | None"] = relationship(
        foreign_keys=[lead_id],
        post_update=True,
    )

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp


class Counterparty(Base):
    __tablename__ = "counterparties"
    __table_args__ = (
        Index(
            "uq_counterparties_org_inn_ul",
            "org_id",
            "inn",
            unique=True,
            sqlite_where=text("type = 'ul' AND inn IS NOT NULL"),
            postgresql_where=text("type = 'ul' AND inn IS NOT NULL"),
        ),
        Index("ix_counterparties_org_name", "org_id", "name"),
        Index("ix_counterparties_org_fio", "org_id", "fio"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[CounterpartyType] = mapped_column(
        Enum(CounterpartyType, name="counterparty_type", **_STR_ENUM),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fio: Mapped[str | None] = mapped_column(String(512), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    kpp: Mapped[str | None] = mapped_column(String(9), nullable=True)
    ogrn: Mapped[str | None] = mapped_column(String(15), nullable=True)
    snils: Mapped[str | None] = mapped_column(String(14), nullable=True)
    passport_series: Mapped[str | None] = mapped_column(String(8), nullable=True)
    passport_number: Mapped[str | None] = mapped_column(String(12), nullable=True)
    passport_issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    passport_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_bik: Mapped[str | None] = mapped_column(String(9), nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bank_corr_account: Mapped[str | None] = mapped_column(String(34), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[CounterpartySource] = mapped_column(
        Enum(CounterpartySource, name="counterparty_source", **_STR_ENUM),
        nullable=False,
        default=CounterpartySource.manual,
    )
    egrul_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    egrul_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(back_populates="counterparties")
    contracts: Mapped[list[Contract]] = relationship(back_populates="counterparty")
    documents: Mapped[list[Document]] = relationship(back_populates="counterparty")
    party_checks: Mapped[list["PartyCheck"]] = relationship(back_populates="counterparty")


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (Index("ix_contracts_org_number", "org_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    counterparty_id: Mapped[int] = mapped_column(
        ForeignKey("counterparties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template: Mapped[str] = mapped_column(String(255), nullable=False)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="contracts")
    counterparty: Mapped[Counterparty] = relationship(back_populates="contracts")
    documents: Mapped[list[Document]] = relationship(back_populates="contract")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_org_created", "org_id", "created_at"),
        Index("ix_documents_org_template", "org_id", "template"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    counterparty_id: Mapped[int | None] = mapped_column(
        ForeignKey("counterparties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    template: Mapped[str] = mapped_column(String(255), nullable=False)
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    format: Mapped[DocumentFormat] = mapped_column(
        Enum(DocumentFormat, name="document_format", **_STR_ENUM),
        nullable=False,
        default=DocumentFormat.docx,
    )
    context: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    organization: Mapped[Organization] = relationship(back_populates="documents")
    contract: Mapped[Contract | None] = relationship(back_populates="documents")
    counterparty: Mapped[Counterparty | None] = relationship(back_populates="documents")
    created_by_user: Mapped[User | None] = relationship(back_populates="documents_created")


class Counter(Base):
    __tablename__ = "counters"

    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suffix: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    organization: Mapped[Organization] = relationship(back_populates="counters")


class OrgField(Base):
    """Словарь пользовательских полей организации (W-41)."""

    __tablename__ = "org_fields"
    __table_args__ = (
        Index("uq_org_fields_org_name", "org_id", "name", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[OrgFieldType] = mapped_column(
        Enum(OrgFieldType, name="org_field_type", **_STR_ENUM),
        nullable=False,
        default=OrgFieldType.string,
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    default_value: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    hint: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    options: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    organization: Mapped[Organization] = relationship(back_populates="org_fields")
    created_by_user: Mapped[User | None] = relationship(
        foreign_keys=[created_by_user_id],
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_org_ts", "org_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization | None] = relationship(back_populates="events")
    user: Mapped[User | None] = relationship(back_populates="events")


class LeadStatus(str, enum.Enum):
    """Статусы заявки с лендинга (W-39)."""

    new = "new"
    invited = "invited"
    registered = "registered"
    rejected = "rejected"
    spam = "spam"


class Lead(Base):
    """Заявки с лендинга (W-08 / W-39)."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    profile: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status", **_STR_ENUM),
        nullable=False,
        default=LeadStatus.new,
        index=True,
    )
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    invite_id: Mapped[int | None] = mapped_column(
        ForeignKey("invites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    admin_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        default=utcnow,
    )

    organization: Mapped[Organization | None] = relationship(
        foreign_keys=[org_id],
    )
    invite: Mapped[Invite | None] = relationship(
        foreign_keys=[invite_id],
    )


class PasswordResetToken(Base):
    """Одноразовые токены восстановления пароля (W-09)."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship()

    def is_expired(self) -> bool:
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return utcnow() >= exp

    @property
    def is_used(self) -> bool:
        return self.used_at is not None


class Tariff(Base):
    """Справочник тарифов SaaS (W-10). Цены — в копейках."""

    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[TariffCode] = mapped_column(
        Enum(TariffCode, name="tariff_code", **_STR_ENUM),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    price_month_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_year_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NULL = без лимита
    limit_documents_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watermark: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blurb: Mapped[str] = mapped_column(Text, nullable=False, default="")
    features: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="tariff")


class TariffPriceLog(Base):
    """История изменений публичных цен и описаний тарифов (W-36)."""

    __tablename__ = "tariff_price_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tariff_id: Mapped[int] = mapped_column(
        ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tariff_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    old_price_month_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_price_month_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    old_price_year_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_price_year_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    old_blurb: Mapped[str] = mapped_column(Text, nullable=False, default="")
    new_blurb: Mapped[str] = mapped_column(Text, nullable=False, default="")
    old_features: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    new_features: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    tariff: Mapped[Tariff] = relationship()
    user: Mapped[User | None] = relationship()


class Subscription(Base):
    """Подписка организации на тариф (W-10)."""

    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_org_status", "org_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tariff_id: Mapped[int] = mapped_column(
        ForeignKey("tariffs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    period: Mapped[SubscriptionPeriod] = mapped_column(
        Enum(SubscriptionPeriod, name="subscription_period", **_STR_ENUM),
        nullable=False,
        default=SubscriptionPeriod.month,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status", **_STR_ENUM),
        nullable=False,
        default=SubscriptionStatus.trial,
        index=True,
    )
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    customer_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rebill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_beta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # W-38: админское «подарочное» продление — вне MRR/выручки
    is_complimentary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(back_populates="subscriptions")
    tariff: Mapped[Tariff] = relationship(back_populates="subscriptions")
    payments: Mapped[list[Payment]] = relationship(back_populates="subscription")

    def is_current(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self.status not in (SubscriptionStatus.trial, SubscriptionStatus.active):
            return False
        end = self.ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return now < end

    def mark_expired(self) -> None:
        self.status = SubscriptionStatus.expired

    def mark_active(self) -> None:
        self.status = SubscriptionStatus.active

    def mark_cancelled(self) -> None:
        self.status = SubscriptionStatus.cancelled
        self.auto_renew = False


class Payment(Base):
    """Платёж: id = UUID = OrderId для Т-Кассы (W-10). Суммы в копейках."""

    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_org_created", "org_id", "created_at"),
        Index("ix_payments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount_kop: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_base_kop: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_kop: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    promo_code_id: Mapped[int | None] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", **_STR_ENUM),
        nullable=False,
        default=PaymentStatus.created,
    )
    tbank_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[PaymentSource] = mapped_column(
        Enum(PaymentSource, name="payment_source", **_STR_ENUM),
        nullable=False,
        default=PaymentSource.card,
    )
    receipt_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_events: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    manual_basis: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(back_populates="payments")
    subscription: Mapped[Subscription | None] = relationship(back_populates="payments")
    promo_code: Mapped["PromoCode | None"] = relationship(back_populates="payments")

    def append_event(self, event: dict) -> None:
        events = list(self.raw_events or [])
        events.append(event)
        self.raw_events = events


class PaymentSettings(Base):
    """Singleton настроек Т-Кассы (строка id=1). Пароль — Fernet (W-13)."""

    __tablename__ = "payment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    terminal_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[PaymentMode] = mapped_column(
        Enum(PaymentMode, name="payment_mode", **_STR_ENUM),
        nullable=False,
        default=PaymentMode.test,
    )
    recurrents_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    taxation: Mapped[str] = mapped_column(String(32), nullable=False, default="usn_income")
    vat_rate: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    default_receipt_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    party_check_daily_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    require_2fa_for_org_admins: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    yandex_login_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # W-39: бета-доступ по умолчанию при создании орг из заявки
    beta_default_tariff: Mapped[str] = mapped_column(
        String(32), nullable=False, default="organization", server_default="organization"
    )
    beta_default_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AnalyticsSettings(Base):
    """Singleton аналитики и подтверждения сайта (W-40), строка id=1."""

    __tablename__ = "analytics_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yandex_metrika_id: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    yandex_metrika_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    yandex_metrika_webvisor: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    yandex_metrika_clickmap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    yandex_metrika_track_forms: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    ga4_measurement_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ga4_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    yandex_webmaster_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    yandex_webmaster_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    google_site_verification: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )
    google_site_verification_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ContentBlock(Base):
    """Публичные CMS-слоты лендинга (W-36)."""

    __tablename__ = "content_blocks"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    versions: Mapped[list["ContentBlockVersion"]] = relationship(
        back_populates="block", cascade="all, delete-orphan"
    )
    user: Mapped[User | None] = relationship()


class ContentBlockVersion(Base):
    """Снимок опубликованной версии CMS-слота."""

    __tablename__ = "content_block_versions"
    __table_args__ = (Index("uq_content_block_versions_key_version", "block_key", "version", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_key: Mapped[str] = mapped_column(
        ForeignKey("content_blocks.key", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    block: Mapped[ContentBlock] = relationship(back_populates="versions")
    user: Mapped[User | None] = relationship()


class PromoCode(Base):
    """Промокод на одну оплату подписки (W-36)."""

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    type: Mapped[PromoCodeType] = mapped_column(
        Enum(PromoCodeType, name="promo_code_type", **_STR_ENUM),
        nullable=False,
        default=PromoCodeType.percent,
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tariff_codes: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    periods: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        onupdate=func.now(),
    )

    payments: Mapped[list[Payment]] = relationship(back_populates="promo_code")


class Announcement(Base):
    """Публичная плашка над шапкой лендинга."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    user: Mapped[User | None] = relationship()


class PartyCheck(Base):
    """Журнал проверок контрагента через DaData findById/party (W-21)."""

    __tablename__ = "party_checks"
    __table_args__ = (
        Index("ix_party_checks_org_checked", "org_id", "checked_at"),
        Index("ix_party_checks_org_inn", "org_id", "inn"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    counterparty_id: Mapped[int | None] = mapped_column(
        ForeignKey("counterparties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    inn: Mapped[str] = mapped_column(String(12), nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)

    organization: Mapped[Organization] = relationship(back_populates="party_checks")
    user: Mapped[User | None] = relationship()
    counterparty: Mapped[Counterparty | None] = relationship(back_populates="party_checks")


class CalendarEvent(Base):
    """Событие календаря / план / напоминание (кабинет организации)."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        Index("ix_calendar_events_org_due", "org_id", "due_on"),
        Index("ix_calendar_events_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[CalendarEventKind] = mapped_column(
        Enum(CalendarEventKind, name="calendar_event_kind", **_STR_ENUM),
        nullable=False,
        default=CalendarEventKind.plan,
    )
    status: Mapped[CalendarEventStatus] = mapped_column(
        Enum(CalendarEventStatus, name="calendar_event_status", **_STR_ENUM),
        nullable=False,
        default=CalendarEventStatus.planned,
    )
    remind_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(
        ForeignKey("counterparties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(back_populates="calendar_events")
    counterparty: Mapped[Counterparty | None] = relationship()
    document: Mapped[Document | None] = relationship()
    contract: Mapped[Contract | None] = relationship()


class LegalAct(Base):
    """Нормативный акт раздела «Законодательство» (W-16)."""

    __tablename__ = "legal_acts"
    __table_args__ = (Index("ix_legal_acts_category_sort", "category", "sort_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[LegalActCategory] = mapped_column(
        Enum(LegalActCategory, name="legal_act_category", **_STR_ENUM),
        nullable=False,
    )
    act_kind: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    adopted_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    authority: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[LegalActStatus] = mapped_column(
        Enum(LegalActStatus, name="legal_act_status", **_STR_ENUM),
        nullable=False,
        default=LegalActStatus.active,
    )
    mode: Mapped[LegalActMode] = mapped_column(
        Enum(LegalActMode, name="legal_act_mode", **_STR_ENUM),
        nullable=False,
        default=LegalActMode.full_text,
    )
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    eo_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    # Для режима fragments: список отслеживаемых статей/глав, напр. ["ст. 79", "ст. 80"]
    tracked_articles: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # W-17: идентификатор документа в ИПС и параметры мониторинга
    ips_nd: Mapped[str | None] = mapped_column(String(32), nullable=True)
    watch_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions: Mapped[list[ActVersion]] = relationship(
        back_populates="act", cascade="all, delete-orphan"
    )
    fragments: Mapped[list[ActFragment]] = relationship(
        back_populates="act", cascade="all, delete-orphan"
    )
    watch_logs: Mapped[list[ActWatchLog]] = relationship(
        back_populates="act", cascade="all, delete-orphan"
    )


class ActVersion(Base):
    """Редакция текста акта (черновик / опубликована / архив)."""

    __tablename__ = "act_versions"
    __table_args__ = (
        Index("ix_act_versions_act_status", "act_id", "status"),
        # Одна опубликованная редакция на акт (PostgreSQL и SQLite).
        Index(
            "uq_act_versions_one_published",
            "act_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
            sqlite_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    change_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # W-22: происхождение текста — html / pdf_extracted / pdf_unrecognized
    text_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[ActVersionStatus] = mapped_column(
        Enum(ActVersionStatus, name="act_version_status", **_STR_ENUM),
        nullable=False,
        default=ActVersionStatus.draft,
    )
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    loaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    act: Mapped[LegalAct] = relationship(back_populates="versions")


class ActFragment(Base):
    """Фрагмент кодекса (экспертные статьи) для режима fragments."""

    __tablename__ = "act_fragments"
    __table_args__ = (
        Index("ix_act_fragments_act_sort", "act_id", "sort_order"),
        Index("uq_act_fragments_act_ref", "act_id", "article_ref", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    act: Mapped[LegalAct] = relationship(back_populates="fragments")


class LegalSearchDoc(Base):
    """Строка поискового индекса НПА (гранулярность — статья/фрагмент, W-22)."""

    __tablename__ = "legal_search_docs"
    __table_args__ = (
        Index("ix_legal_search_docs_act", "act_id"),
        Index("ix_legal_search_docs_version", "version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("act_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    fragment_id: Mapped[int | None] = mapped_column(
        ForeignKey("act_fragments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    article_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    heading: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requisites: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # PostgreSQL: tsvector (триггер); SQLite: TEXT-заглушка
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR().with_variant(Text(), "sqlite"),
        nullable=True,
    )
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    act: Mapped[LegalAct] = relationship()
    version: Mapped[ActVersion | None] = relationship()
    fragment: Mapped[ActFragment | None] = relationship()


class ActWatchLog(Base):
    """Журнал ежедневного мониторинга официального опубликования."""

    __tablename__ = "act_watch_log"
    __table_args__ = (Index("ix_act_watch_log_act_checked", "act_id", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    result: Mapped[ActWatchResult] = mapped_column(
        Enum(ActWatchResult, name="act_watch_result", **_STR_ENUM),
        nullable=False,
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("act_versions.id", ondelete="SET NULL"), nullable=True
    )

    act: Mapped[LegalAct] = relationship(back_populates="watch_logs")


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobType(str, enum.Enum):
    document_pdf = "document_pdf"
    package_pdf = "package_pdf"
    package_zip = "package_zip"


class Job(Base):
    """Фоновые задачи генерации (W-30)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_org_created", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type", **_STR_ENUM), nullable=False, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", **_STR_ENUM),
        nullable=False,
        default=JobStatus.pending,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RateLimitHit(Base):
    """События rate limit для скользящего окна (T2, multi-replica)."""

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        Index("ix_rate_limit_hits_bucket_key_created", "bucket", "key", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class OpsJournalEntry(Base):
    """Журнал действий раздела «Сервер» (W-34)."""

    __tablename__ = "ops_journal"
    __table_args__ = (
        Index("ix_ops_journal_created", "created_at"),
        Index("ix_ops_journal_action", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class LawBookmark(Base):
    """Личная закладка на акт или статью (W-35)."""

    __tablename__ = "law_bookmarks"
    __table_args__ = (
        Index("uq_law_bookmarks_user_key", "user_id", "bookmark_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fragment_id: Mapped[int | None] = mapped_column(
        ForeignKey("act_fragments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Уникальный ключ без NULL: «{act_id}:0» или «{act_id}:{fragment_id}»
    bookmark_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class LawNote(Base):
    """Личная заметка к статье (W-35, платный тариф)."""

    __tablename__ = "law_notes"
    __table_args__ = (
        Index("uq_law_notes_user_fragment", "user_id", "fragment_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fragment_id: Mapped[int] = mapped_column(
        ForeignKey("act_fragments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class LawWatch(Base):
    """Слежение за актом: письмо при публикации новой редакции (W-35, платный)."""

    __tablename__ = "law_watches"
    __table_args__ = (
        Index("uq_law_watches_user_act", "user_id", "act_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class LawView(Base):
    """История просмотров актов в кабинете (W-35)."""

    __tablename__ = "law_views"
    __table_args__ = (Index("ix_law_views_user_viewed", "user_id", "viewed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fragment_id: Mapped[int | None] = mapped_column(
        ForeignKey("act_fragments.id", ondelete="SET NULL"), nullable=True
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class LawWatchNotice(Base):
    """Очередь уведомлений о публикации для дайджеста (W-35)."""

    __tablename__ = "law_watch_notices"
    __table_args__ = (
        Index("ix_law_watch_notices_user_sent", "user_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    act_id: Mapped[int] = mapped_column(
        ForeignKey("legal_acts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("act_versions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
