"""Удаление организаций и пользователей из админки сервиса."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CalendarEvent,
    Contract,
    Counter,
    Counterparty,
    Document,
    Event,
    Invite,
    Job,
    Lead,
    Organization,
    PartyCheck,
    Payment,
    PaymentStatus,
    Subscription,
    User,
    UserRole,
)

try:
    from app.models import OrgField as _OrgField
except ImportError:  # pragma: no cover
    _OrgField = None  # type: ignore
from app.services.audit import record_event

log = logging.getLogger("dok.admin_delete")


class AdminDeleteError(Exception):
    """Ошибка удаления с текстом для UI."""


def delete_organization(
    db: Session,
    *,
    org: Organization,
    actor: User,
) -> None:
    """Удалить организацию и связанные данные (необратимо)."""
    org_id = org.id
    org_name = org.name
    user_ids = list(
        db.scalars(select(User.id).where(User.org_id == org_id)).all()
    )
    # Нельзя удалить орг, если среди её пользователей — текущий админ
    if actor.id in user_ids:
        raise AdminDeleteError(
            "Нельзя удалить организацию, к которой привязана ваша учётная запись."
        )

    # HOTFIX: незавершённые платежи у банка — сначала Cancel/GetState, потом удаление.
    pending = list(
        db.scalars(
            select(Payment).where(
                Payment.org_id == org_id,
                Payment.status.in_((PaymentStatus.created, PaymentStatus.authorized)),
                Payment.tbank_payment_id.is_not(None),
            )
        ).all()
    )
    if pending:
        sample = ", ".join(str(p.id) for p in pending[:3])
        more = f" и ещё {len(pending) - 3}" if len(pending) > 3 else ""
        raise AdminDeleteError(
            "Нельзя удалить организацию: есть незавершённые платежи Т-Кассы "
            f"({len(pending)} шт.: {sample}{more}). "
            "Сначала отмените или доведите их у банка (GetState/Cancel)."
        )

    # contracts → counterparties (FK RESTRICT на counterparty_id)
    db.execute(delete(Contract).where(Contract.org_id == org_id))
    models = [
        Document,
        Job,
        PartyCheck,
        CalendarEvent,
        Counterparty,
        Counter,
        Event,
        Invite,
        Payment,
        Subscription,
    ]
    if _OrgField is not None:
        models.insert(0, _OrgField)
    for model in models:
        db.execute(delete(model).where(model.org_id == org_id))

    # заявки: отвязать, не удалять историю лидов
    for lead in db.scalars(select(Lead).where(Lead.org_id == org_id)).all():
        lead.org_id = None
        lead.invite_id = None

    # пользователи организации (oauth/reset каскадом)
    if user_ids:
        db.execute(delete(User).where(User.id.in_(user_ids)))

    if org.source_lead_id:
        org.source_lead_id = None
        db.flush()

    record_event(
        db,
        type="org_deleted",
        org_id=None,
        user_id=actor.id,
        details={"org_id": org_id, "name": org_name, "users": user_ids},
        commit=False,
    )
    db.delete(org)
    db.commit()

    files_root = Path(get_settings().files_root)
    org_dir = files_root / str(org_id)
    if org_dir.is_dir():
        try:
            shutil.rmtree(org_dir)
        except OSError:
            log.exception("Не удалось удалить каталог файлов org=%s", org_id)


def delete_user(
    db: Session,
    *,
    target: User,
    actor: User,
) -> None:
    """Удалить учётную запись (необратимо)."""
    if target.id == actor.id:
        raise AdminDeleteError("Нельзя удалить свою учётную запись.")

    if target.role == UserRole.service_admin:
        others = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == UserRole.service_admin,
                User.id != target.id,
                User.is_active.is_(True),
            )
        ) or 0
        if others < 1:
            raise AdminDeleteError(
                "Нельзя удалить последнего активного администратора сервиса."
            )

    email = target.email
    org_id = target.org_id
    record_event(
        db,
        type="user_deleted",
        org_id=org_id,
        user_id=actor.id,
        details={"deleted_user_id": target.id, "email": email},
        commit=False,
    )
    db.delete(target)
    db.commit()
