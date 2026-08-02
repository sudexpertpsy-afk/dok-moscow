"""Админка раздела «Законодательство» (W-19)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ActVersion,
    ActVersionStatus,
    ActWatchLog,
    ActWatchResult,
    LegalAct,
    LegalActCategory,
    LegalActMode,
    LegalActStatus,
    utcnow,
)
from app.services.legal_monitor import consecutive_source_errors
from app.services.legal_registry import (
    create_draft_version,
    published_version,
    publish_version,
    reject_version,
)
from app.services.sources.diff_text import html_diff_preview, ndiff_to_html, paragraph_diff


class ActHealth(str, Enum):
    green = "green"  # сверено < 30 дней, нет черновика/ошибок
    yellow = "yellow"  # ждёт подтверждения черновик
    red = "red"  # ошибка источника
    gray = "gray"  # нет сверки / не мониторится


HEALTH_LABEL = {
    ActHealth.green: "Сверено",
    ActHealth.yellow: "Ждёт подтверждения",
    ActHealth.red: "Ошибка источника",
    ActHealth.gray: "Нет сверки",
}


@dataclass
class ActAdminRow:
    act: LegalAct
    health: ActHealth
    draft: ActVersion | None
    published: ActVersion | None
    last_watch: ActWatchLog | None


def _slugify(title: str) -> str:
    raw = (title or "").strip().lower()
    raw = re.sub(r"[^\w\s\-а-яё]+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[\s_]+", "-", raw)
    return (raw[:140] or "act").strip("-")


def act_health(db: Session, act: LegalAct) -> ActHealth:
    draft = db.scalar(
        select(ActVersion)
        .where(ActVersion.act_id == act.id, ActVersion.status == ActVersionStatus.draft)
        .order_by(ActVersion.id.desc())
    )
    if draft is not None:
        return ActHealth.yellow
    if act.watch_enabled and consecutive_source_errors(db, act.id) >= 3:
        return ActHealth.red
    last = db.scalar(
        select(ActWatchLog)
        .where(ActWatchLog.act_id == act.id)
        .order_by(ActWatchLog.id.desc())
        .limit(1)
    )
    if last is not None and last.result == ActWatchResult.source_error:
        return ActHealth.red
    if act.last_verified_at and act.last_verified_at >= utcnow() - timedelta(days=30):
        return ActHealth.green
    return ActHealth.gray


def list_acts_admin(db: Session) -> list[ActAdminRow]:
    acts = db.scalars(
        select(LegalAct).order_by(LegalAct.sort_order, LegalAct.id)
    ).all()
    rows: list[ActAdminRow] = []
    for act in acts:
        draft = db.scalar(
            select(ActVersion)
            .where(ActVersion.act_id == act.id, ActVersion.status == ActVersionStatus.draft)
            .order_by(ActVersion.id.desc())
        )
        pub = published_version(db, act.id)
        last = db.scalar(
            select(ActWatchLog)
            .where(ActWatchLog.act_id == act.id)
            .order_by(ActWatchLog.id.desc())
            .limit(1)
        )
        rows.append(
            ActAdminRow(
                act=act,
                health=act_health(db, act),
                draft=draft,
                published=pub,
                last_watch=last,
            )
        )
    # Жёлтые и красные сверху
    order = {ActHealth.yellow: 0, ActHealth.red: 1, ActHealth.gray: 2, ActHealth.green: 3}
    rows.sort(key=lambda r: (order[r.health], r.act.sort_order, r.act.id))
    return rows


def get_act_admin(db: Session, act_id: int) -> LegalAct | None:
    return db.scalar(
        select(LegalAct)
        .options(
            joinedload(LegalAct.versions),
            joinedload(LegalAct.fragments),
            joinedload(LegalAct.watch_logs),
        )
        .where(LegalAct.id == act_id)
    )


def draft_diff_html(db: Session, act: LegalAct, draft: ActVersion) -> str:
    if draft.diff_text:
        return ndiff_to_html(draft.diff_text)
    pub = published_version(db, act.id)
    if pub is None:
        return ndiff_to_html(None)
    return html_diff_preview(pub.body_html, draft.body_html)


def do_publish(db: Session, version_id: int, user_id: int) -> ActVersion:
    version = db.get(ActVersion, version_id)
    if version is None:
        raise ValueError("Редакция не найдена")
    return publish_version(db, version, reviewed_by_user_id=user_id)


@dataclass
class PublishBatchItem:
    act_id: int
    slug: str
    version_id: int | None = None
    ok: bool = False
    skipped: str | None = None
    error: str | None = None


@dataclass
class PublishBatchReport:
    items: list[PublishBatchItem]

    @property
    def ok_count(self) -> int:
        return sum(1 for i in self.items if i.ok)

    @property
    def skip_count(self) -> int:
        return sum(1 for i in self.items if i.skipped)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.items if i.error)


def publish_all_drafts(
    db: Session,
    *,
    user_id: int | None,
    min_body_chars: int = 40,
    only_without_published: bool = False,
) -> PublishBatchReport:
    """Опубликовать последний непустой черновик по каждому акту (пакетно).

    Осознанное действие владельца/админа: не автомат мониторинга.
    """
    acts = db.scalars(select(LegalAct).order_by(LegalAct.sort_order, LegalAct.id)).all()
    items: list[PublishBatchItem] = []
    for act in acts:
        if only_without_published and published_version(db, act.id) is not None:
            items.append(
                PublishBatchItem(
                    act_id=act.id, slug=act.slug, skipped="уже есть published"
                )
            )
            continue
        draft = db.scalar(
            select(ActVersion)
            .where(
                ActVersion.act_id == act.id,
                ActVersion.status == ActVersionStatus.draft,
            )
            .order_by(ActVersion.id.desc())
        )
        if draft is None:
            continue
        body = (draft.body_html or "").strip()
        # убрать теги для оценки длины
        plain = re.sub(r"<[^>]+>", " ", body)
        plain = re.sub(r"\s+", " ", plain).strip()
        if len(plain) < min_body_chars:
            items.append(
                PublishBatchItem(
                    act_id=act.id,
                    slug=act.slug,
                    version_id=draft.id,
                    skipped=f"пустой/короткий текст ({len(plain)} симв.)",
                )
            )
            continue
        try:
            publish_version(db, draft, reviewed_by_user_id=user_id)
            db.flush()
            items.append(
                PublishBatchItem(
                    act_id=act.id, slug=act.slug, version_id=draft.id, ok=True
                )
            )
        except Exception as exc:  # noqa: BLE001 — пакетный отчёт
            db.rollback()
            items.append(
                PublishBatchItem(
                    act_id=act.id,
                    slug=act.slug,
                    version_id=draft.id,
                    error=str(exc),
                )
            )
    return PublishBatchReport(items=items)


def do_reject(db: Session, version_id: int, user_id: int) -> ActVersion:
    version = db.get(ActVersion, version_id)
    if version is None:
        raise ValueError("Редакция не найдена")
    return reject_version(db, version, reviewed_by_user_id=user_id)


def manual_upload(
    db: Session,
    *,
    act_id: int,
    body_html: str,
    source: str,
    user_id: int,
    revision_date=None,
    change_basis: str | None = None,
) -> ActVersion:
    source = (source or "").strip()
    body_html = (body_html or "").strip()
    if not source:
        raise ValueError("Укажите источник текста")
    if not body_html:
        raise ValueError("Текст редакции пуст")
    act = db.get(LegalAct, act_id)
    if act is None:
        raise ValueError("Акт не найден")
    pub = published_version(db, act_id)
    diff = paragraph_diff(pub.body_html if pub else "", body_html) if pub else ""
    basis = (change_basis or "").strip() or f"Ручная загрузка; источник: {source}"
    if source not in basis:
        basis = f"{basis}; источник: {source}"
    return create_draft_version(
        db,
        act_id=act_id,
        body_html=body_html,
        revision_date=revision_date,
        change_basis=basis,
        loaded_by_user_id=user_id,
        diff_text=diff or None,
        text_origin="manual",
    )


def update_act_settings(
    db: Session,
    act: LegalAct,
    *,
    mode: str,
    status: str,
    watch_enabled: bool,
    tracked_articles_raw: str,
    notes: str | None,
    source_url: str,
) -> LegalAct:
    act.mode = LegalActMode(mode)
    act.status = LegalActStatus(status)
    act.watch_enabled = bool(watch_enabled)
    articles = [a.strip() for a in (tracked_articles_raw or "").splitlines() if a.strip()]
    act.tracked_articles = articles
    act.notes = (notes or "").strip() or None
    act.source_url = (source_url or "").strip()
    if act.mode == LegalActMode.fragments:
        from app.services.legal_registry import _ensure_fragment_stubs

        _ensure_fragment_stubs(db, act)
    db.flush()
    return act


def create_act(
    db: Session,
    *,
    title: str,
    slug: str,
    category: str,
    mode: str,
    number: str = "",
    act_kind: str = "",
    authority: str = "",
    source_url: str = "",
    watch_enabled: bool = False,
    tracked_articles_raw: str = "",
) -> LegalAct:
    title = (title or "").strip()
    if not title:
        raise ValueError("Укажите название акта")
    slug = (slug or "").strip() or _slugify(title)
    if db.scalar(select(LegalAct).where(LegalAct.slug == slug)):
        raise ValueError(f"Slug уже занят: {slug}")
    articles = [a.strip() for a in (tracked_articles_raw or "").splitlines() if a.strip()]
    act = LegalAct(
        title=title,
        slug=slug,
        category=LegalActCategory(category),
        mode=LegalActMode(mode),
        number=(number or "").strip(),
        act_kind=(act_kind or "").strip(),
        authority=(authority or "").strip() or "Российская Федерация",
        source_url=(source_url or "").strip(),
        status=LegalActStatus.active,
        sort_order=900,
        tracked_articles=articles,
        watch_enabled=bool(watch_enabled),
    )
    db.add(act)
    db.flush()
    if act.mode == LegalActMode.fragments:
        from app.services.legal_registry import _ensure_fragment_stubs

        _ensure_fragment_stubs(db, act)
    return act
