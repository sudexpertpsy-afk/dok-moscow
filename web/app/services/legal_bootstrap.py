"""Первичное наполнение реестра НПА из ИПС / publication PDF (после W-19).

Тексты приходят как черновики — публикация только вручную в админке.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ActFragment,
    ActVersion,
    ActVersionStatus,
    LegalAct,
    LegalActMode,
    LegalActStatus,
)
from app.services.legal_registry import create_draft_version, published_version
from app.services.legal_search import ingest_pdf_version, split_html_articles
from app.services.sources.http_client import ThrottledClient
from app.services.sources.html_page_loader import HtmlPageLoaderError, load_html_page
from app.services.sources.ips_loader import IpsLoaderError, load_ips_document
from app.services.sources.publication_api import PublicationClient
from app.services.sources.diff_text import paragraph_diff
from app.services.legal_registry import INITIAL_REGISTRY

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


def _archive_pending_drafts(db: Session, act_id: int) -> None:
    for d in db.scalars(
        select(ActVersion).where(
            ActVersion.act_id == act_id,
            ActVersion.status == ActVersionStatus.draft,
        )
    ).all():
        d.status = ActVersionStatus.archived


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
        _archive_pending_drafts(db, act.id)

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


def legal_pdf_storage_path(act: LegalAct, eo_number: str) -> Path:
    root = Path(get_settings().files_root) / "legal" / act.slug
    root.mkdir(parents=True, exist_ok=True)
    safe_eo = re.sub(r"[^\w.-]+", "_", eo_number or "doc")
    return root / f"{safe_eo}.pdf"


def pull_publication_pdf_to_draft(
    db: Session,
    act: LegalAct,
    *,
    http: ThrottledClient | None = None,
    user_id: int | None = None,
    eo_number: str | None = None,
    replace_draft: bool = False,
) -> PullResult:
    """Скачать PDF с publication.pravo.gov.ru → черновик через ingest_pdf_version."""
    slug = act.slug
    if act.mode == LegalActMode.card:
        return PullResult(act.id, slug, ok=False, skipped="режим card — только ссылка")
    if act.status != LegalActStatus.active:
        return PullResult(act.id, slug, ok=False, skipped="акт не active")

    eo = (eo_number or act.eo_number or "").strip()
    if not eo:
        return PullResult(act.id, slug, ok=False, skipped="нет eo_number")

    if has_pending_draft(db, act.id) and not replace_draft:
        return PullResult(act.id, slug, ok=False, skipped="уже есть черновик")

    if replace_draft:
        _archive_pending_drafts(db, act.id)

    owns = http is None
    client = http or ThrottledClient()
    try:
        pub = PublicationClient(http=client)
        pdf_bytes = pub.download_pdf(eo)
        path = legal_pdf_storage_path(act, eo)
        path.write_bytes(pdf_bytes)
        meta_name = ""
        try:
            meta = pub.get_document(eo)
            meta_name = str(meta.get("complexName") or meta.get("name") or "").strip()
        except Exception:
            meta_name = ""
        draft = ingest_pdf_version(
            db,
            act_id=act.id,
            pdf_path=str(path),
            change_basis=(
                f"Первичное наполнение / PDF publication.pravo.gov.ru; eoNumber={eo}"
            ),
            loaded_by_user_id=user_id,
        )
        if act.eo_number != eo:
            act.eo_number = eo
        # скан без текстового слоя: карточка со ссылкой на официальный PDF
        plain = re.sub(r"<[^>]+>", " ", draft.body_html or "")
        plain = re.sub(r"\s+", " ", plain).strip()
        if draft.text_origin == "pdf_unrecognized" or len(plain) < 40:
            pdf_href = pub.pdf_url(eo)
            title = _escape(meta_name or act.title)
            draft.body_html = (
                f"<p><strong>{title}</strong></p>"
                f"<p>Официальный текст опубликован в виде PDF "
                f"(скан без текстового слоя; полнотекстовый поиск по странице недоступен).</p>"
                f"<p>Скачать официальную публикацию: "
                f'<a href="{_escape(pdf_href)}" rel="noopener noreferrer" target="_blank">'
                f"publication.pravo.gov.ru · eoNumber={_escape(eo)}</a>.</p>"
                f"<p>Реквизиты акта в реестре Док.Москва: {_escape(act.act_kind)} "
                f"№ {_escape(act.number or '—')}.</p>"
            )
            draft.text_origin = "pdf_unrecognized"
            if "скан" not in (draft.change_basis or "").casefold():
                draft.change_basis = (
                    (draft.change_basis or "PDF publication")
                    + " · скан без текстового слоя, карточка со ссылкой"
                )
            db.flush()
        n_frag = 0
        if act.mode == LegalActMode.fragments and (draft.body_html or "").strip():
            n_frag = fill_fragments_from_html(db, act, draft.body_html)
        return PullResult(
            act_id=act.id,
            slug=slug,
            ok=True,
            draft_id=draft.id,
            fragments_filled=n_frag,
        )
    except Exception as exc:
        log.exception("Publication PDF pull failed act=%s eo=%s", slug, eo)
        return PullResult(act.id, slug, ok=False, error=str(exc))
    finally:
        if owns:
            client.close()


def registry_row(slug: str) -> dict | None:
    for row in INITIAL_REGISTRY:
        if row.get("slug") == slug:
            return row
    return None


def pull_seed_url_to_draft(
    db: Session,
    act: LegalAct,
    *,
    http: ThrottledClient | None = None,
    user_id: int | None = None,
    replace_draft: bool = False,
    seed_url: str | None = None,
) -> PullResult:
    """Скачать HTML по seed_url из реестра → черновик."""
    slug = act.slug
    if act.mode == LegalActMode.card:
        return PullResult(act.id, slug, ok=False, skipped="режим card — только ссылка")
    if act.status != LegalActStatus.active:
        return PullResult(act.id, slug, ok=False, skipped="акт не active")

    row = registry_row(slug) or {}
    url = (seed_url or row.get("seed_url") or "").strip()
    if not url:
        return PullResult(act.id, slug, ok=False, skipped="нет seed_url")

    if has_pending_draft(db, act.id) and not replace_draft:
        return PullResult(act.id, slug, ok=False, skipped="уже есть черновик")
    if replace_draft:
        _archive_pending_drafts(db, act.id)

    try:
        doc = load_html_page(url, http=http)
        body_html = (doc.body_html or "").strip()
        if len(body_html) < 80:
            raise HtmlPageLoaderError("Пустой текст")
    except HtmlPageLoaderError as exc:
        return PullResult(act.id, slug, ok=False, error=str(exc))
    except Exception as exc:
        log.exception("seed_url pull failed act=%s", slug)
        return PullResult(act.id, slug, ok=False, error=str(exc))

    pub = published_version(db, act.id)
    diff = paragraph_diff(pub.body_html, body_html) if pub else None
    draft = create_draft_version(
        db,
        act_id=act.id,
        body_html=body_html,
        change_basis=(
            f"Первичное наполнение / HTML seed_url; источник: {doc.source_url}"
        ),
        loaded_by_user_id=user_id,
        diff_text=diff,
        text_origin="seed_html",
    )
    n_frag = fill_fragments_from_html(db, act, body_html)
    return PullResult(
        act_id=act.id,
        slug=slug,
        ok=True,
        draft_id=draft.id,
        fragments_filled=n_frag,
    )


def pull_link_card_to_draft(
    db: Session,
    act: LegalAct,
    *,
    user_id: int | None = None,
    replace_draft: bool = False,
) -> PullResult:
    """Черновик-карточка со ссылкой на первоисточник (когда полного текста нет в API)."""
    slug = act.slug
    if act.mode == LegalActMode.card:
        return PullResult(act.id, slug, ok=False, skipped="режим card — только ссылка")
    if has_pending_draft(db, act.id) and not replace_draft:
        return PullResult(act.id, slug, ok=False, skipped="уже есть черновик")
    if replace_draft:
        _archive_pending_drafts(db, act.id)
    src = act.source_url or "официальный первоисточник"
    body = (
        f"<p><strong>{_escape(act.title)}</strong></p>"
        f"<p>{_escape(act.act_kind)}"
        f"{(' № ' + _escape(act.number)) if act.number else ''}."
        f"{(' Принят: ' + act.adopted_on.isoformat()) if act.adopted_on else ''}</p>"
        f"<p>Полный текст в автоматических источниках (ИПС / publication API) "
        f"недоступен — откройте первоисточник и при необходимости загрузите "
        f"редакцию вручную в админке законодательства.</p>"
        f"<p>Официальный источник: "
        f'<a href="{_escape(src)}" rel="noopener noreferrer" target="_blank">'
        f"{_escape(src)}</a>.</p>"
        f"<p>{_escape(act.notes or '')}</p>"
    )
    draft = create_draft_version(
        db,
        act_id=act.id,
        body_html=body,
        change_basis="Карточка со ссылкой на первоисточник (нет полного текста в API)",
        loaded_by_user_id=user_id,
        text_origin="link_card",
    )
    return PullResult(act_id=act.id, slug=slug, ok=True, draft_id=draft.id)


def pull_act_to_draft(
    db: Session,
    act: LegalAct,
    *,
    http: ThrottledClient | None = None,
    user_id: int | None = None,
    replace_draft: bool = False,
) -> PullResult:
    """ИПС → PDF publication → seed_url → карточка-ссылка."""
    if act.ips_nd:
        return pull_ips_to_draft(
            db,
            act,
            http=http,
            user_id=user_id,
            replace_draft=replace_draft,
        )
    if act.eo_number:
        return pull_publication_pdf_to_draft(
            db,
            act,
            http=http,
            user_id=user_id,
            replace_draft=replace_draft,
        )
    row = registry_row(act.slug) or {}
    if row.get("seed_url"):
        return pull_seed_url_to_draft(
            db,
            act,
            http=http,
            user_id=user_id,
            replace_draft=replace_draft,
        )
    if row.get("seed_link_card"):
        return pull_link_card_to_draft(
            db, act, user_id=user_id, replace_draft=replace_draft
        )
    return PullResult(
        act.id, act.slug, ok=False, skipped="нет ips_nd, eo_number и seed_url"
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
    """Пройти акты с ips_nd/eo_number без опубликованной редакции (или все с источником)."""
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
            row = registry_row(act.slug) or {}
            if (
                not act.ips_nd
                and not act.eo_number
                and not row.get("seed_url")
                and not row.get("seed_link_card")
            ):
                report.results.append(
                    PullResult(
                        act.id,
                        act.slug,
                        ok=False,
                        skipped="нет ips_nd, eo_number и seed_url",
                    )
                )
                continue
            if only_without_published and published_version(db, act.id) is not None:
                report.results.append(
                    PullResult(act.id, act.slug, ok=False, skipped="уже опубликовано")
                )
                continue
            result = pull_act_to_draft(
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
    """Активные акты без published и с возможностью наполнения."""
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
