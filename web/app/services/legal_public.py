"""Публичный каталог и поиск раздела «Законодательство» (W-18 / W-22)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ActVersionStatus,
    LegalAct,
    LegalActCategory,
    LegalActStatus,
)
from app.services.legal_search import (
    LegalSearchFilters,
    LegalSearchHit,
    LegalSearchResult,
    search_legal,
)

CATEGORY_GROUPS: list[tuple[LegalActCategory, str]] = [
    (LegalActCategory.law, "Профильный закон и отраслевые законы"),
    (LegalActCategory.code, "Процессуальные кодексы"),
    (LegalActCategory.plenum, "Разъяснения высших судов"),
    (LegalActCategory.order, "Ведомственные приказы"),
    (LegalActCategory.standard, "Стандарты"),
]

CATEGORY_LABEL = {c: label for c, label in CATEGORY_GROUPS}


@dataclass
class SearchHit:
    """Совместимость с W-18: обёртка над LegalSearchHit."""

    act: LegalAct
    snippet: str
    rank: float
    article_ref: str = ""
    heading: str = ""
    url: str = ""
    direct: bool = False


def list_catalog(db: Session) -> list[tuple[str, list[LegalAct]]]:
    acts = db.scalars(
        select(LegalAct)
        .where(LegalAct.status == LegalActStatus.active)
        .order_by(LegalAct.sort_order, LegalAct.id)
    ).all()
    by_cat: dict[LegalActCategory, list[LegalAct]] = {c: [] for c, _ in CATEGORY_GROUPS}
    for act in acts:
        by_cat.setdefault(act.category, []).append(act)
    return [(label, by_cat.get(cat, [])) for cat, label in CATEGORY_GROUPS if by_cat.get(cat)]


def get_act_by_slug(db: Session, slug: str) -> LegalAct | None:
    return db.scalar(
        select(LegalAct)
        .options(
            joinedload(LegalAct.versions),
            joinedload(LegalAct.fragments),
        )
        .where(LegalAct.slug == slug)
    )


def published_for(act: LegalAct):
    for v in act.versions or []:
        if v.status == ActVersionStatus.published:
            return v
    return None


def archived_versions(act: LegalAct) -> list:
    rows = [v for v in (act.versions or []) if v.status == ActVersionStatus.archived]
    rows.sort(key=lambda v: (v.revision_date or v.created_at), reverse=True)
    return rows


def format_act_meta(act: LegalAct) -> str:
    parts = []
    if act.act_kind:
        parts.append(act.act_kind)
    if act.adopted_on:
        parts.append(act.adopted_on.strftime("%d.%m.%Y"))
    if act.number:
        parts.append(f"№ {act.number}")
    return " · ".join(parts)


def highlight(text: str, query: str) -> str:
    """Подсветка; если уже есть <mark> от ts_headline — не трогаем."""
    if not query or not text:
        return text
    if "<mark>" in text:
        return text
    pattern = re.compile(re.escape(query), re.I)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


def _to_search_hit(hit: LegalSearchHit) -> SearchHit:
    return SearchHit(
        act=hit.act,
        snippet=hit.snippet,
        rank=hit.rank,
        article_ref=hit.article_ref,
        heading=hit.heading,
        url=hit.url,
        direct=hit.direct,
    )


def search_acts(
    db: Session,
    query: str,
    *,
    limit: int = 30,
    offset: int = 0,
    category: str | None = None,
    authority: str | None = None,
    status: str | None = None,
    revision_from=None,
    revision_to=None,
) -> list[SearchHit]:
    """Обёртка search_legal для публичного /zakon и обратной совместимости."""
    result = search_legal(
        db,
        query,
        LegalSearchFilters(
            category=category,
            authority=authority,
            status=status,
            revision_from=revision_from,
            revision_to=revision_to,
        ),
        limit=limit,
        offset=offset,
    )
    return [_to_search_hit(h) for h in result.hits]


def search_acts_grouped(
    db: Session,
    query: str,
    *,
    limit: int = 30,
    offset: int = 0,
    **filter_kwargs,
) -> LegalSearchResult:
    return search_legal(
        db,
        query,
        LegalSearchFilters(**filter_kwargs),
        limit=limit,
        offset=offset,
    )


# Шаблоны документов → рекомендуемые акты (сквозные ссылки в кабинете)
TEMPLATE_NORMATIVE: dict[str, list[str]] = {
    "договор": ["73-fz-sudebno-ekspertnaya-deyatelnost", "gpk-ekspertiza"],
    "экспертиз": ["73-fz-sudebno-ekspertnaya-deyatelnost", "gpk-ekspertiza", "upk-ekspertiza"],
    "гпд": ["uk-otvetstvennost-eksperta"],
    "акт": ["73-fz-sudebno-ekspertnaya-deyatelnost"],
}


def normative_for_template(template_name: str) -> list[str]:
    name = (template_name or "").casefold()
    slugs: list[str] = []
    for key, vals in TEMPLATE_NORMATIVE.items():
        if key in name:
            for s in vals:
                if s not in slugs:
                    slugs.append(s)
    if not slugs:
        slugs = ["73-fz-sudebno-ekspertnaya-deyatelnost"]
    return slugs
