"""Публичный каталог и поиск раздела «Законодательство» (W-18)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ActFragment,
    ActVersion,
    ActVersionStatus,
    LegalAct,
    LegalActCategory,
    LegalActMode,
    LegalActStatus,
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
    act: LegalAct
    snippet: str
    rank: float


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


def published_for(act: LegalAct) -> ActVersion | None:
    for v in act.versions or []:
        if v.status == ActVersionStatus.published:
            return v
    return None


def archived_versions(act: LegalAct) -> list[ActVersion]:
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


def _snippet(text: str, query: str, radius: int = 120) -> str:
    plain = re.sub(r"<[^>]+>", " ", text or "")
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return ""
    q = query.casefold()
    idx = plain.casefold().find(q)
    if idx < 0:
        return plain[: radius * 2] + ("…" if len(plain) > radius * 2 else "")
    start = max(0, idx - radius)
    end = min(len(plain), idx + len(query) + radius)
    chunk = plain[start:end]
    if start > 0:
        chunk = "…" + chunk
    if end < len(plain):
        chunk = chunk + "…"
    return chunk


def highlight(text: str, query: str) -> str:
    if not query or not text:
        return text
    pattern = re.compile(re.escape(query), re.I)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


def search_acts(db: Session, query: str, *, limit: int = 30) -> list[SearchHit]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    dialect = db.get_bind().dialect.name

    act_rows = db.scalars(
        select(LegalAct).where(
            LegalAct.status == LegalActStatus.active,
            or_(
                LegalAct.title.ilike(like),
                LegalAct.number.ilike(like),
                LegalAct.notes.ilike(like),
            ),
        )
    ).all()

    hits: dict[int, SearchHit] = {}
    for act in act_rows:
        hits[act.id] = SearchHit(act=act, snippet=act.title, rank=2.0)

    # тексты опубликованных редакций
    if dialect == "postgresql":
        ts_query = func.plainto_tsquery("russian", q)
        version_stmt = (
            select(ActVersion, LegalAct)
            .join(LegalAct, LegalAct.id == ActVersion.act_id)
            .where(
                LegalAct.status == LegalActStatus.active,
                ActVersion.status == ActVersionStatus.published,
                func.to_tsvector("russian", ActVersion.body_html).op("@@")(ts_query),
            )
            .limit(limit)
        )
        for version, act in db.execute(version_stmt).all():
            snip = _snippet(version.body_html, q)
            prev = hits.get(act.id)
            rank = 3.0
            if prev is None or rank > prev.rank:
                hits[act.id] = SearchHit(act=act, snippet=snip or act.title, rank=rank)
    else:
        versions = db.execute(
            select(ActVersion, LegalAct)
            .join(LegalAct, LegalAct.id == ActVersion.act_id)
            .where(
                LegalAct.status == LegalActStatus.active,
                ActVersion.status == ActVersionStatus.published,
                ActVersion.body_html.ilike(like),
            )
            .limit(limit)
        ).all()
        for version, act in versions:
            hits[act.id] = SearchHit(
                act=act, snippet=_snippet(version.body_html, q), rank=3.0
            )

    frags = db.execute(
        select(ActFragment, LegalAct)
        .join(LegalAct, LegalAct.id == ActFragment.act_id)
        .where(
            LegalAct.status == LegalActStatus.active,
            or_(
                ActFragment.article_ref.ilike(like),
                ActFragment.title.ilike(like),
                ActFragment.body_html.ilike(like),
            ),
        )
        .limit(limit)
    ).all()
    for frag, act in frags:
        snip = _snippet(frag.body_html or frag.title or frag.article_ref, q)
        prev = hits.get(act.id)
        if prev is None or 2.5 > prev.rank:
            hits[act.id] = SearchHit(act=act, snippet=snip, rank=2.5)

    ordered = sorted(hits.values(), key=lambda h: (-h.rank, h.act.sort_order, h.act.title))
    return ordered[:limit]


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
