"""Автоудаление файлов документов по сроку хранения организации (W-09)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, Organization
from app.services.audit import record_event
from app.services.settings_svc import ensure_requisites

log = logging.getLogger("dok.retention")

DEFAULT_RETENTION_DAYS = 1095  # ~3 года
RETENTION_SECTION = "хранение"
RETENTION_KEY = "срок_дней_файлов"


@dataclass
class PurgeStats:
    orgs: int = 0
    deleted_files: int = 0
    deleted_rows: int = 0
    skipped: int = 0
    errors: int = 0


def get_retention_days(org: Organization) -> int:
    """0 или отрицательное — не удалять. По умолчанию 1095."""
    req = org.requisites or {}
    block = req.get(RETENTION_SECTION) or {}
    raw = block.get(RETENTION_KEY, DEFAULT_RETENTION_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return days


def set_retention_days(org: Organization, days: int) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    import copy

    req = ensure_requisites(org)
    block = dict(req.get(RETENTION_SECTION) or {})
    block[RETENTION_KEY] = int(days)
    req[RETENTION_SECTION] = block
    org.requisites = copy.deepcopy(req)
    flag_modified(org, "requisites")


def purge_expired_documents(
    db: Session,
    files_root: str | Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PurgeStats:
    """Удалить файлы и записи Document старше срока хранения каждой org."""
    root = Path(files_root)
    now = now or datetime.now(timezone.utc)
    stats = PurgeStats()
    orgs = db.scalars(select(Organization)).all()

    for org in orgs:
        days = get_retention_days(org)
        if days <= 0:
            stats.skipped += 1
            continue
        stats.orgs += 1
        cutoff = now - timedelta(days=days)
        docs = db.scalars(
            select(Document).where(
                Document.org_id == org.id,
                Document.created_at < cutoff,
            )
        ).all()
        for doc in docs:
            path = root / doc.file_path if doc.file_path else None
            try:
                if path and path.is_file():
                    if not dry_run:
                        path.unlink()
                    stats.deleted_files += 1
                if not dry_run:
                    db.delete(doc)
                stats.deleted_rows += 1
            except OSError:
                log.exception("Не удалось удалить файл документа #%s", doc.id)
                stats.errors += 1

        if not dry_run and docs:
            record_event(
                db,
                type="retention_purge",
                org_id=org.id,
                details={
                    "days": days,
                    "cutoff": cutoff.isoformat(),
                    "deleted": len(docs),
                    "dry_run": False,
                },
                commit=False,
            )

    if not dry_run:
        db.commit()
    return stats
