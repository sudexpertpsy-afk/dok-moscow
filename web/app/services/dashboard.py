"""Стартовая панель кабинета (W-28)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    CalendarEvent,
    CalendarEventStatus,
    Contract,
    Document,
    LegalAct,
    LegalActStatus,
)
from app.services.limits import usage_snapshot


@dataclass
class AttentionItem:
    title: str
    url: str
    subtitle: str = ""


@dataclass
class DashboardData:
    docs_month: int
    packages_month: int
    usage_docs: int
    limit_docs: int | None
    watermark: bool
    attention: list[AttentionItem] = field(default_factory=list)
    calendar: list[CalendarEvent] = field(default_factory=list)
    legal_news: list[LegalAct] = field(default_factory=list)
    empty_org: bool = False


def load_dashboard(db: Session, org_id: int) -> DashboardData:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    docs_month = int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.org_id == org_id, Document.created_at >= month_start)
        )
        or 0
    )
    # «комплекты» — документы, созданные пачкой (есть contract_id) за месяц, грубо по уникальным contract
    packages_month = int(
        db.scalar(
            select(func.count(func.distinct(Document.contract_id))).where(
                Document.org_id == org_id,
                Document.created_at >= month_start,
                Document.contract_id.is_not(None),
            )
        )
        or 0
    )

    snap = usage_snapshot(db, org_id)
    attention: list[AttentionItem] = []

    soon = date.today() + timedelta(days=14)
    expiring = db.scalars(
        select(Contract)
        .options(joinedload(Contract.counterparty))
        .where(
            Contract.org_id == org_id,
            Contract.ends_on.is_not(None),
            Contract.ends_on <= soon,
            Contract.ends_on >= date.today(),
        )
        .order_by(Contract.ends_on.asc())
        .limit(10)
    ).all()
    for c in expiring:
        name = ""
        if c.counterparty:
            name = c.counterparty.name or c.counterparty.fio or ""
        attention.append(
            AttentionItem(
                title=f"Договор {c.number} истекает {c.ends_on.strftime('%d.%m.%Y')}",
                url=f"/cabinet/journal?counterparty_id={c.counterparty_id}",
                subtitle=name,
            )
        )

    # договоры без акта: есть contract, нет документа с «Акт» в шаблоне
    contracts = db.scalars(
        select(Contract).where(Contract.org_id == org_id).order_by(Contract.id.desc()).limit(50)
    ).all()
    if contracts:
        cids = [c.id for c in contracts]
        with_act = set(
            db.scalars(
                select(Document.contract_id).where(
                    Document.org_id == org_id,
                    Document.contract_id.in_(cids),
                    Document.template.ilike("%акт%"),
                )
            ).all()
        )
        for c in contracts:
            if c.id not in with_act:
                attention.append(
                    AttentionItem(
                        title=f"Договор {c.number} без акта",
                        url="/cabinet/journal",
                        subtitle="Создайте акт в комплекте",
                    )
                )
            if len(attention) >= 12:
                break

    # «неоплаченные счета» — документы-счета за 90 дней (нет отдельного статуса оплаты клиента)
    since = now - timedelta(days=90)
    bills = db.scalars(
        select(Document)
        .where(
            Document.org_id == org_id,
            Document.created_at >= since,
            or_(
                Document.template.ilike("%счёт%"),
                Document.template.ilike("%счет%"),
            ),
        )
        .order_by(Document.created_at.desc())
        .limit(5)
    ).all()
    for d in bills:
        attention.append(
            AttentionItem(
                title=f"Счёт {d.number or d.id}",
                url=f"/cabinet/documents/{d.id}",
                subtitle=d.template,
            )
        )

    cal = list(
        db.scalars(
            select(CalendarEvent)
            .where(
                CalendarEvent.org_id == org_id,
                CalendarEvent.status != CalendarEventStatus.done,
                CalendarEvent.due_on >= date.today(),
            )
            .order_by(CalendarEvent.due_on.asc())
            .limit(5)
        ).all()
    )

    legal = list(
        db.scalars(
            select(LegalAct)
            .where(LegalAct.status == LegalActStatus.active)
            .order_by(LegalAct.updated_at.desc(), LegalAct.id.desc())
            .limit(3)
        ).all()
    )

    empty_org = docs_month == 0 and not contracts

    return DashboardData(
        docs_month=docs_month,
        packages_month=packages_month,
        usage_docs=snap.documents_this_month,
        limit_docs=snap.limits.limit_documents_month,
        watermark=snap.limits.watermark,
        attention=attention[:15],
        calendar=cal,
        legal_news=legal,
        empty_org=empty_org,
    )


def legal_public_href(act: LegalAct) -> str:
    """Ссылка на акт из дашборда — кабинетная версия (W-35)."""
    return f"/cabinet/zakon/{act.slug}"
