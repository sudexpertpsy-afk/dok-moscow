"""Первичное наполнение реестра НПА из ИПС (после W-19).

Тексты приходят как черновики — публикация только вручную в админке.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActFragment,
    ActVersion,
    ActVersionStatus,
    LegalAct,
    LegalActMode,
    LegalActStatus,
)
from app.services.legal_registry import create_draft_version, published_version
from app.services.legal_search import split_html_articles
from app.services.sources.http_client import ThrottledClient
from app.services.sources.ips_loader import IpsLoaderError, load_ips_document
from app.services.sources.diff_text import paragraph_diff

log = logging.getLogger("dok.legal.bootstrap")


@dataclass
class PullResult:
    act_id: int
    slug: str
    ok: bool
    draft_id: int | None = None
    fragments_filled: int = 0
    error: str | None = None
    skipped: str | None = None


@dataclass
class BootstrapReport:
    results: list[PullResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok and not r.skipped)

    @property
    def skip_count(self) -> int:
        return sum(1 for r in self.results if r.skipped)


def _norm_ref(ref: str) -> str:
    s = (ref or "").casefold().replace("статья", "ст.")
    s = re.sub(r"\s+", " ", s).strip()
    # ст.87.1 / ст. 87.1
    s = re.sub(r"ст\.\s*", "ст. ", s)
    return s


def _article_num(ref: str) -> str | None:
    m = re.search(r"ст\.\s*([\d.]+)", ref or "", flags=re.I)
    return m.group(1) if m else None


def fill_fragments_from_html(db: Session, act: LegalAct, body_html: str) -> int:
    """Заполнить пустые/все body фрагментов по разбиению полного HTML."""
    if act.mode != LegalActMode.fragments:
        return 0
    parts = split_html_articles(body_html)
    by_num: dict[str, tuple[str, str, str]] = {}
    for ref, heading, text in parts:
        num = _article_num(ref)
        if num:
            by_num[num] = (ref, heading, text)

    frags = list(
        db.scalars(select(ActFragment).where(ActFragment.act_id == act.id)).all()
    )
    filled = 0
    for frag in frags:
        num = _article_num(frag.article_ref)
        if not num or num not in by_num:
            # главы вроде «гл. 27 (…)» — пропускаем
            continue
        _ref, heading, text = by_num[num]
        if not text.strip():
            continue
        # HTML-обёртка абзацев из plain text
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        frag.body_html = "".join(f"<p>{_escape(p)}</p>" for p in paras) or f"<p>{_escape(text)}</p>"
        if heading and (not frag.title or frag.title == frag.article_ref):
            frag.title = heading
        filled += 1
    db.flush()
    return filled


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def has_pending_draft(db: Session, act_id: int) -> bool:
    return (
        db.scalar(
            select(ActVersion.id).where(
                ActVersion.act_id == act_id,
                ActVersion.status == ActVersionStatus.draft,
            )
        )
        is not None
    )


def pull_ips_to_draft(
    db: Session,
    act: LegalAct,
    *,
    http: ThrottledClient | None = None,
    user_id: int | None = None,
    body_html: str | None = None,
    source_url: str | None = None,
    replace_draft: bool = False,
) -> PullResult:
    """Скачать текст ИПС (или принять body_html) → черновик + заполнение фрагментов."""
    slug = act.slug
    if act.mode == LegalActMode.card:
        return PullResult(act.id, slug, ok=False, skipped="режим card — только ссылка")
    if act.status != LegalActStatus.active:
        return PullResult(act.id, slug, ok=False, skipped="акт не active")

    if has_pending_draft(db, act.id) and not replace_draft:
        return PullResult(act.id, slug, ok=False, skipped="уже есть черновик")

    if replace_draft:
        for d in db.scalars(
            select(ActVersion).where(
                ActVersion.act_id == act.id,
                ActVersion.status == ActVersionStatus.draft,
            )
        ).all():
            d.status = ActVersionStatus.archived

    try:
        if body_html is None:
            if not act.ips_nd:
                return PullResult(act.id, slug, ok=False, skipped="нет ips_nd")
            doc = load_ips_document(act.ips_nd, http=http)
            body_html = doc.body_html
            source_url = source_url or doc.source_url
        body_html = (body_html or "").strip()
        if len(body_html) < 20:
            raise IpsLoaderError("Пустой текст")
    except IpsLoaderError as exc:
        return PullResult(act.id, slug, ok=False, error=str(exc))
    except Exception as exc:
        log.exception("IPS pull failed act=%s", slug)
        return PullResult(act.id, slug, ok=False, error=str(exc))

    pub = published_version(db, act.id)
    diff = paragraph_diff(pub.body_html, body_html) if pub else None
    src = source_url or act.source_url or (f"ИПС nd={act.ips_nd}" if act.ips_nd else "bootstrap")
    draft = create_draft_version(
        db,
        act_id=act.id,
        body_html=body_html,
        change_basis=f"Первичное наполнение / подтягивание из ИПС; источник: {src}",
        loaded_by_user_id=user_id,
        diff_text=diff,
        text_origin="ips_bootstrap",
    )
    n_frag = fill_fragments_from_html(db, act, body_html)
    return PullResult(
        act_id=act.id,
        slug=slug,
        ok=True,
        draft_id=draft.id,
        fragments_filled=n_frag,
    )


def bootstrap_missing(
    db: Session,
    *,
    http: ThrottledClient | None = None,
    user_id: int | None = None,
    only_without_published: bool = True,
    limit: int = 50,
    replace_draft: bool = False,
) -> BootstrapReport:
    """Пройти акты с ips_nd без опубликованной редакции (или все с nd)."""
    acts = db.scalars(
        select(LegalAct)
        .where(LegalAct.status == LegalActStatus.active)
        .order_by(LegalAct.sort_order, LegalAct.id)
    ).all()
    owns = http is None
    client = http or ThrottledClient()
    report = BootstrapReport()
    try:
        for act in acts:
            if len([r for r in report.results if r.ok]) >= limit:
                break
            if not act.ips_nd:
                report.results.append(
                    PullResult(act.id, act.slug, ok=False, skipped="нет ips_nd")
                )
                continue
            if only_without_published and published_version(db, act.id) is not None:
                report.results.append(
                    PullResult(act.id, act.slug, ok=False, skipped="уже опубликовано")
                )
                continue
            result = pull_ips_to_draft(
                db,
                act,
                http=client,
                user_id=user_id,
                replace_draft=replace_draft,
            )
            report.results.append(result)
            db.flush()
    finally:
        if owns:
            client.close()
    return report


def acts_needing_fill(db: Session) -> list[LegalAct]:
    """Активные акты без published и с возможностью наполнения (ips_nd или fragments)."""
    out: list[LegalAct] = []
    for act in db.scalars(
        select(LegalAct)
        .where(LegalAct.status == LegalActStatus.active)
        .order_by(LegalAct.sort_order)
    ).all():
        if published_version(db, act.id) is not None:
            continue
        if act.mode == LegalActMode.card:
            continue
        out.append(act)
    return out
