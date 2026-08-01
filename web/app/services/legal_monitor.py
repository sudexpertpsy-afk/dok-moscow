"""Ежедневный мониторинг официального опубликования (W-17)."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActVersion,
    ActVersionStatus,
    ActWatchLog,
    ActWatchResult,
    LegalAct,
    LegalActMode,
    LegalActStatus,
    utcnow,
)
from app.services.legal_mail import notify_legal_change, notify_legal_source_errors
from app.services.legal_registry import create_draft_version, published_version
from app.services.sources.diff_text import paragraph_diff
from app.services.sources.http_client import ThrottledClient
from app.services.sources.ips_loader import IpsLoaderError, load_ips_document
from app.services.sources.publication_api import PublicationClient, PublicationDocument

log = logging.getLogger("dok.legal_monitor")


def consecutive_source_errors(db: Session, act_id: int) -> int:
    rows = db.scalars(
        select(ActWatchLog)
        .where(ActWatchLog.act_id == act_id)
        .order_by(ActWatchLog.checked_at.desc(), ActWatchLog.id.desc())
        .limit(10)
    ).all()
    n = 0
    for row in rows:
        if row.result == ActWatchResult.source_error:
            n += 1
        else:
            break
    return n


def _looks_like_amendment(doc: PublicationDocument, act: LegalAct) -> bool:
    blob = f"{doc.name}\n{doc.complex_name}".casefold()
    markers = ("внесении изменений", "о внесении изменения", "признании утративш")
    if any(m in blob for m in markers):
        return True
    # прямое опубликование самого акта / новой редакции номера
    num = (act.number or "").casefold()
    if num and num in (doc.number or "").casefold():
        return True
    return False


def _already_seen_eo(db: Session, act_id: int, eo: str) -> bool:
    if not eo:
        return False
    rows = db.scalars(
        select(ActWatchLog)
        .where(ActWatchLog.act_id == act_id)
        .order_by(ActWatchLog.id.desc())
        .limit(50)
    ).all()
    for row in rows:
        if eo in (row.details or ""):
            return True
    drafts = db.scalars(
        select(ActVersion).where(
            ActVersion.act_id == act_id,
            ActVersion.change_basis.is_not(None),
        )
    ).all()
    for d in drafts:
        if eo in (d.change_basis or ""):
            return True
    return False


def check_act(
    db: Session,
    act: LegalAct,
    *,
    pub: PublicationClient,
    http: ThrottledClient,
    since: date | None = None,
    send_mail: bool = True,
) -> ActWatchLog:
    """Проверить один акт. Ошибка источника не пробрасывается наружу."""
    since = since or (date.today() - timedelta(days=14))
    watch_name = (act.watch_name or "").strip()
    try:
        if not act.watch_enabled or act.status != LegalActStatus.active:
            log_row = ActWatchLog(
                act_id=act.id,
                checked_at=utcnow(),
                result=ActWatchResult.unchanged,
                details="watch_disabled",
            )
            db.add(log_row)
            act.last_checked_at = utcnow()
            db.flush()
            return log_row

        if not watch_name:
            log_row = ActWatchLog(
                act_id=act.id,
                checked_at=utcnow(),
                result=ActWatchResult.unchanged,
                details="no_watch_name",
            )
            db.add(log_row)
            act.last_checked_at = utcnow()
            db.flush()
            return log_row

        found = pub.find_changes_mentioning(watch_name=watch_name, since=since, page_size=10)
        amendments = [d for d in found if _looks_like_amendment(d, act)]
        new_docs = [d for d in amendments if not _already_seen_eo(db, act.id, d.eo_number)]

        if not new_docs:
            log_row = ActWatchLog(
                act_id=act.id,
                checked_at=utcnow(),
                result=ActWatchResult.unchanged,
                details=f"checked={len(found)}; amendments={len(amendments)}",
            )
            db.add(log_row)
            act.last_checked_at = utcnow()
            db.flush()
            return log_row

        # берём самый свежий
        doc = new_docs[0]
        body_html = ""
        diff_text = ""
        if act.ips_nd and act.mode != LegalActMode.card:
            try:
                ips_doc = load_ips_document(act.ips_nd, http=http)
                body_html = ips_doc.body_html
                current = published_version(db, act.id)
                if current and current.body_html:
                    diff_text = paragraph_diff(current.body_html, body_html)
            except IpsLoaderError as exc:
                body_html = (
                    f"<p>Обнаружена публикация {doc.eo_number}, "
                    f"но текст ИПС пока не загружен: {exc}</p>"
                )
                diff_text = ""

        basis = (
            f"{doc.complex_name or doc.name} · eoNumber={doc.eo_number} · "
            f"опубликован {doc.view_date or doc.publish_date or '—'}"
        )
        draft = create_draft_version(
            db,
            act_id=act.id,
            body_html=body_html or f"<p>{basis}</p>",
            change_basis=basis,
            revision_date=date.today(),
            diff_text=diff_text or None,
        )
        log_row = ActWatchLog(
            act_id=act.id,
            checked_at=utcnow(),
            result=ActWatchResult.change_found,
            details=f"eo={doc.eo_number}; draft={draft.id}",
            draft_version_id=draft.id,
        )
        db.add(log_row)
        act.last_checked_at = utcnow()
        db.flush()
        if send_mail:
            notify_legal_change(
                act_title=act.title,
                act_slug=act.slug,
                change_summary=basis + ("\n\n" + diff_text[:2000] if diff_text else ""),
                draft_version_id=draft.id,
                eo_numbers=[doc.eo_number],
            )
        return log_row
    except Exception as exc:
        log.exception("legal watch error act=%s", act.slug)
        log_row = ActWatchLog(
            act_id=act.id,
            checked_at=utcnow(),
            result=ActWatchResult.source_error,
            details=str(exc)[:1000],
        )
        db.add(log_row)
        act.last_checked_at = utcnow()
        db.flush()
        errors = consecutive_source_errors(db, act.id)
        if send_mail and errors >= 3:
            notify_legal_source_errors(
                act_title=act.title,
                act_slug=act.slug,
                error_count=errors,
                last_detail=str(exc)[:500],
            )
        return log_row


def run_daily_watch(
    db: Session,
    *,
    pub: PublicationClient | None = None,
    http: ThrottledClient | None = None,
    since: date | None = None,
    send_mail: bool = True,
) -> dict[str, int]:
    """Обойти все отслеживаемые акты. Возвращает счётчики результатов."""
    owns_pub = pub is None
    owns_http = http is None
    http = http or ThrottledClient()
    pub = pub or PublicationClient(http=http)
    stats = {"unchanged": 0, "change_found": 0, "source_error": 0, "skipped": 0}
    try:
        acts = db.scalars(
            select(LegalAct)
            .where(LegalAct.status == LegalActStatus.active)
            .order_by(LegalAct.sort_order, LegalAct.id)
        ).all()
        for act in acts:
            if not act.watch_enabled:
                stats["skipped"] += 1
                continue
            row = check_act(
                db, act, pub=pub, http=http, since=since, send_mail=send_mail
            )
            key = row.result.value
            stats[key] = stats.get(key, 0) + 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_pub:
            pub.close()
        elif owns_http:
            http.close()
    return stats
