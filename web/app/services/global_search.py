"""Сквозной глобальный поиск по ролям и тарифу (W-23)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.models import (
    Contract,
    Counterparty,
    Document,
    Lead,
    Organization,
    Payment,
    TariffCode,
)
from app.navigation import match_registry, resolve_nav, roles_for_user, sitemap_for
from app.services.billing import get_tariff_limits


@dataclass
class SearchGroupItem:
    title: str
    url: str
    subtitle: str = ""
    badge: str = ""
    upsell: bool = False


@dataclass
class SearchGroup:
    key: str
    title: str
    items: list[SearchGroupItem] = field(default_factory=list)


@dataclass
class GlobalSearchResult:
    query: str
    groups: list[SearchGroup] = field(default_factory=list)
    sitemap: list[tuple[str, list[dict]]] = field(default_factory=list)
    empty: bool = True


def _tariff_for(db: Session, user: CurrentUser) -> TariffCode | None:
    if user.org_id is None:
        return None
    return get_tariff_limits(db, user.org_id).tariff_code


def _roles(user: CurrentUser):
    return roles_for_user(is_service_admin=user.is_service_admin, has_org=user.org_id is not None)


def global_search(db: Session, user: CurrentUser, query: str, *, limit: int = 8) -> GlobalSearchResult:
    q = (query or "").strip()
    roles = _roles(user)
    tariff = _tariff_for(db, user)
    result = GlobalSearchResult(query=q)

    sm = sitemap_for(roles=roles, tariff=tariff)
    result.sitemap = [
        (
            name,
            [
                {
                    "title": r.title,
                    "url": r.url,
                    "upsell": r.upsell,
                    "badge": f"доступно на тарифе «{r.upsell_tariff_label}»" if r.upsell else "",
                }
                for r in items
            ],
        )
        for name, items in sm
    ]

    if len(q) < 2:
        result.empty = True
        return result

    groups: list[SearchGroup] = []

    resolved = resolve_nav(
        roles=roles,
        tariff=tariff,
        area="search",
        menu_only=False,
        include_upsell=True,
        include_quick=True,
    )
    nav_hits = match_registry(q, resolved)
    if nav_hits:
        sections: list[SearchGroupItem] = []
        actions: list[SearchGroupItem] = []
        for r in nav_hits[:limit]:
            item = SearchGroupItem(
                title=r.title,
                url=r.url,
                subtitle=" · ".join(r.item.synonyms[:3]),
                badge=f"доступно на тарифе «{r.upsell_tariff_label}»" if r.upsell else "",
                upsell=r.upsell,
            )
            if r.item.quick_action:
                actions.append(item)
            else:
                sections.append(item)
        if actions:
            groups.append(SearchGroup(key="actions", title="Быстрые действия", items=actions))
        if sections:
            groups.append(SearchGroup(key="sections", title="Разделы и функции", items=sections))

    if user.org_id is not None:
        groups.extend(_search_org_entities(db, user.org_id, q, limit=limit))

    try:
        from app.services.legal_search import search_legal

        legal = search_legal(db, q, limit=limit)
        if legal.hits:
            groups.append(
                SearchGroup(
                    key="legal",
                    title="Законодательство",
                    items=[
                        SearchGroupItem(
                            title=h.article_ref or h.heading or h.act.title,
                            url=h.url,
                            subtitle=h.act.title,
                        )
                        for h in legal.hits[:limit]
                    ],
                )
            )
    except Exception:
        pass

    if user.is_service_admin:
        groups.extend(_search_admin(db, q, limit=limit))

    result.groups = [g for g in groups if g.items]
    result.empty = not result.groups
    return result


def _search_org_entities(db: Session, org_id: int, q: str, *, limit: int) -> list[SearchGroup]:
    like = f"%{q}%"
    groups: list[SearchGroup] = []

    cps = db.scalars(
        select(Counterparty)
        .where(
            Counterparty.org_id == org_id,
            or_(
                Counterparty.name.ilike(like),
                Counterparty.fio.ilike(like),
                Counterparty.inn.ilike(like),
                Counterparty.ogrn.ilike(like),
            ),
        )
        .order_by(Counterparty.id.desc())
        .limit(limit)
    ).all()
    if cps:
        groups.append(
            SearchGroup(
                key="counterparties",
                title="Контрагенты",
                items=[
                    SearchGroupItem(
                        title=c.name or c.fio or f"#{c.id}",
                        url=f"/cabinet/counterparties/{c.id}",
                        subtitle=c.inn or "",
                    )
                    for c in cps
                ],
            )
        )

    docs = db.scalars(
        select(Document)
        .where(
            Document.org_id == org_id,
            or_(Document.template.ilike(like), Document.number.ilike(like)),
        )
        .order_by(Document.created_at.desc())
        .limit(limit)
    ).all()
    if docs:
        groups.append(
            SearchGroup(
                key="documents",
                title="Документы",
                items=[
                    SearchGroupItem(
                        title=d.template,
                        url=f"/cabinet/documents/{d.id}",
                        subtitle=d.number or d.created_at.strftime("%d.%m.%Y"),
                    )
                    for d in docs
                ],
            )
        )

    contracts = db.scalars(
        select(Contract)
        .where(
            Contract.org_id == org_id,
            or_(Contract.template.ilike(like), Contract.number.ilike(like)),
        )
        .order_by(Contract.id.desc())
        .limit(limit)
    ).all()
    if contracts:
        groups.append(
            SearchGroup(
                key="contracts",
                title="Договоры",
                items=[
                    SearchGroupItem(
                        title=c.number or c.template,
                        url="/cabinet/journal",
                        subtitle=c.template,
                    )
                    for c in contracts
                ],
            )
        )

    try:
        from app.services.templates import list_templates_for_org

        tpls = [
            t
            for t in list_templates_for_org(org_id)
            if q.casefold() in (t.get("name") or "").casefold()
            or q.casefold() in (t.get("title") or "").casefold()
            or q.casefold() in (t.get("stem") or "").casefold()
            or q.casefold() in (t.get("description") or "").casefold()
        ][:limit]
        if tpls:
            groups.append(
                SearchGroup(
                    key="templates",
                    title="Шаблоны",
                    items=[
                        SearchGroupItem(
                            title=t.get("title") or t.get("stem") or t.get("name") or "",
                            url=f"/cabinet/documents/new/{t['name']}",
                            subtitle=str(t.get("description") or t.get("source") or ""),
                        )
                        for t in tpls
                    ],
                )
            )
    except Exception:
        pass

    return groups


def _search_admin(db: Session, q: str, *, limit: int) -> list[SearchGroup]:
    like = f"%{q}%"
    groups: list[SearchGroup] = []

    orgs = db.scalars(
        select(Organization)
        .where(Organization.name.ilike(like))
        .order_by(Organization.id.desc())
        .limit(limit)
    ).all()
    if orgs:
        groups.append(
            SearchGroup(
                key="organizations",
                title="Организации-подписчики",
                items=[
                    SearchGroupItem(
                        title=o.name,
                        url=f"/admin/organizations/{o.id}",
                        subtitle=f"id {o.id}",
                    )
                    for o in orgs
                ],
            )
        )

    pay_rows = db.scalars(
        select(Payment)
        .where(
            or_(
                Payment.purpose.ilike(like),
                Payment.tbank_payment_id.ilike(like),
                Payment.manual_basis.ilike(like),
            )
        )
        .order_by(Payment.created_at.desc())
        .limit(limit)
    ).all()
    if not pay_rows and q.isdigit():
        pay_rows = db.scalars(
            select(Payment).where(Payment.amount_kop == int(q)).limit(limit)
        ).all()
    if pay_rows:
        groups.append(
            SearchGroup(
                key="payments",
                title="Платежи",
                items=[
                    SearchGroupItem(
                        title=f"Платёж {p.id}",
                        url=f"/admin/payments/{p.id}",
                        subtitle=f"{getattr(p.status, 'value', p.status)} · {p.amount_kop / 100:.2f} ₽",
                    )
                    for p in pay_rows
                ],
            )
        )

    leads = db.scalars(
        select(Lead)
        .where(
            or_(
                Lead.email.ilike(like),
                Lead.profile.ilike(like),
                Lead.comment.ilike(like),
            )
        )
        .order_by(Lead.id.desc())
        .limit(limit)
    ).all()
    if leads:
        groups.append(
            SearchGroup(
                key="leads",
                title="Заявки с лендинга",
                items=[
                    SearchGroupItem(
                        title=lead.email,
                        url="/admin/leads",
                        subtitle=lead.profile or "",
                    )
                    for lead in leads
                ],
            )
        )

    return groups
