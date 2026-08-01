"""Модели данных Док.Москва (Приложение А ТЗ, пакеты W-01/W-02)."""

from __future__ import annotations

import enum
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    service_admin = "service_admin"
    user = "user"


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

    users: Mapped[list[User]] = relationship(back_populates="organization")
    invites: Mapped[list[Invite]] = relationship(back_populates="organization")
    counterparties: Mapped[list[Counterparty]] = relationship(back_populates="organization")
    contracts: Mapped[list[Contract]] = relationship(back_populates="organization")
    documents: Mapped[list[Document]] = relationship(back_populates="organization")
    counters: Mapped[list[Counter]] = relationship(back_populates="organization")
    events: Mapped[list[Event]] = relationship(back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", **_STR_ENUM),
        nullable=False,
        default=UserRole.user,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization | None] = relationship(back_populates="users")
    documents_created: Mapped[list[Document]] = relationship(back_populates="created_by_user")
    events: Mapped[list[Event]] = relationship(back_populates="user")


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

    organization: Mapped[Organization] = relationship(back_populates="invites")

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


class Lead(Base):
    """Заявки с лендинга (W-08) — без org_id."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    profile: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
