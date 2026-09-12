"""W-50 B.3: удаление пустых организаций (guard + purge)."""

from __future__ import annotations

import logging
import shutil
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Counterparty,
    Document,
    Invite,
    Lead,
    Organization,
    Payment,
    PaymentStatus,
    Signup,
    User,
    utcnow,
)
from app.services.audit import record_event
from app.services.safe_paths import org_files_root

log = logging.getLogger("dok.org_purge")

PURGE_MIN_AGE = timedelta(days=7)


class OrgPurgeError(Exception):
    """Ошибка purge с текстом для UI."""


def can_purge_org(db: Session, org: Organization) -> tuple[bool, str]:
    """Можно ли удалить пустую организацию. Возвращает (ok, reason)."""
    org_id = org.id
    users_n = int(
        db.scalar(select(func.count()).select_from(User).where(User.org_id == org_id)) or 0
    )
    if users_n > 0:
        return False, f"есть пользователи ({users_n})"

    docs_n = int(
        db.scalar(select(func.count()).select_from(Document).where(Document.org_id == org_id))
        or 0
    )
    if docs_n > 0:
        return False, f"есть документы ({docs_n})"

    cps_n = int(
        db.scalar(
            select(func.count()).select_from(Counterparty).where(Counterparty.org_id == org_id)
        )
        or 0
    )
    if cps_n > 0:
        return False, f"есть контрагенты ({cps_n})"

    confirmed = db.scalar(
        select(Payment.id).where(
            Payment.org_id == org_id,
            Payment.status == PaymentStatus.confirmed,
        ).limit(1)
    )
    if confirmed is not None:
        return False, "есть подтверждённые платежи"

    now = utcnow()
    lead_ids = select(Lead.id).where(Lead.org_id == org_id)
    active_inv = db.scalar(
        select(Invite.id)
        .where(
            Invite.is_active.is_(True),
            Invite.used_at.is_(None),
            Invite.expires_at > now,
            or_(
                Invite.org_id == org_id,
                Invite.lead_id.in_(lead_ids),
            ),
        )
        .limit(1)
    )
    if active_inv is not None:
        return False, "есть активный инвайт"

    created = org.created_at
    if created is not None:
        if created.tzinfo is None:
            from datetime import timezone

            created = created.replace(tzinfo=timezone.utc)
        if now - created < PURGE_MIN_AGE:
            return False, "организации меньше 7 дней"

    return True, ""


def purge_org(
    db: Session,
    org: Organization,
    *,
    actor_id: int | None,
    reason: str = "empty",
) -> None:
    """Удалить файлы org и саму организацию (cascade), событие org.purged."""
    ok, why = can_purge_org(db, org)
    if not ok:
        raise OrgPurgeError(why or "нельзя удалить организацию")

    org_id = org.id
    org_name = org.name
    created_at = org.created_at.isoformat() if org.created_at else None
    source = "lead_invite" if org.source_lead_id else "other"

    try:
        root = org_files_root(org_id)
        if root.exists():
            shutil.rmtree(root)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("purge files org=%s: %s", org_id, exc)

    record_event(
        db,
        type="org.purged",
        org_id=None,
        user_id=actor_id,
        details={
            "org_id": org_id,
            "org_name": org_name,
            "reason": reason,
            "created_at": created_at,
            "source": source,
        },
        commit=False,
    )
    db.delete(org)
    db.commit()


def list_purge_candidates(db: Session) -> list[tuple[Organization, str, int]]:
    """Кандидаты на purge: (org, reason_for_ui, age_days). reason пустой если can_purge."""
    now = utcnow()
    out: list[tuple[Organization, str, int]] = []
    orgs = list(db.scalars(select(Organization).order_by(Organization.id)).all())
    for org in orgs:
        ok, why = can_purge_org(db, org)
        if not ok:
            continue
        created = org.created_at
        age_days = 0
        if created is not None:
            if created.tzinfo is None:
                from datetime import timezone

                created = created.replace(tzinfo=timezone.utc)
            age_days = max(0, int((now - created).total_seconds() // 86400))
        out.append((org, why or "empty", age_days))
    return out


def get_purge_mode(db: Session) -> str:
    from app.services.billing import ensure_payment_settings

    row = ensure_payment_settings(db)
    mode = (row.purge_mode or "dry").strip().lower()
    return mode if mode in ("dry", "live") else "dry"


def set_purge_mode(db: Session, mode: str, *, actor_id: int | None) -> str:
    from app.services.billing import ensure_payment_settings

    mode = (mode or "").strip().lower()
    if mode not in ("dry", "live"):
        raise OrgPurgeError("purge_mode: ожидается dry или live")
    row = ensure_payment_settings(db)
    old = (row.purge_mode or "dry").strip().lower()
    if old != mode:
        row.purge_mode = mode
        row.purge_mode_changed_at = utcnow()
        record_event(
            db,
            type="settings.purge_mode_changed",
            org_id=None,
            user_id=actor_id,
            details={"from": old, "to": mode},
            commit=False,
        )
        db.commit()
    return mode


def purge_empty_orgs_daily(db: Session) -> dict[str, int]:
    """W-50 / W-50.1: суточная чистка (dry — только кандидаты) + Signup >7 дней."""
    mode = get_purge_mode(db)
    purged = 0
    candidates = 0
    orgs = list(db.scalars(select(Organization).order_by(Organization.id)).all())
    for org in orgs:
        ok, why = can_purge_org(db, org)
        if not ok:
            continue
        created = org.created_at
        age_days = 0
        if created is not None:
            if created.tzinfo is None:
                from datetime import timezone

                created = created.replace(tzinfo=timezone.utc)
            age_days = max(0, int((utcnow() - created).total_seconds() // 86400))
        reason = why or "empty"
        if mode == "dry":
            candidates += 1
            log.info(
                "purge_candidate org=%s reason=%s age_days=%s",
                org.id,
                reason,
                age_days,
            )
            record_event(
                db,
                type="org.purge_candidate",
                org_id=None,
                user_id=None,
                details={
                    "org_id": org.id,
                    "reason": reason,
                    "age_days": age_days,
                },
                commit=False,
            )
            continue
        try:
            purge_org(db, org, actor_id=None, reason=reason)
            purged += 1
        except OrgPurgeError as exc:
            log.info("purge skip org=%s: %s", org.id, exc)

    if mode == "dry" and candidates:
        db.commit()

    signups_expired = 0
    cutoff = utcnow() - timedelta(days=7)
    old_signups = list(
        db.scalars(select(Signup).where(Signup.created_at < cutoff)).all()
    )
    for row in old_signups:
        record_event(
            db,
            type="signup.expired",
            org_id=None,
            user_id=None,
            details={"signup_id": row.id, "email": row.email},
            commit=False,
        )
        db.delete(row)
        signups_expired += 1
    if old_signups:
        db.commit()

    return {
        "purged": purged,
        "candidates": candidates,
        "signups_expired": signups_expired,
        "mode": mode,
    }
