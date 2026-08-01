"""Сотрудники организации: инвайты, роли, деактивация (W-27)."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Invite, OrgRole, Organization, User, UserRole, utcnow
from app.org_roles import is_org_admin
from app.services.audit import record_event
from app.services.limits import assert_can_add_user
from app.services.mail import send_email


def list_org_users(db: Session, org_id: int) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.org_id == org_id, User.role == UserRole.user)
            .order_by(User.created_at.asc(), User.id.asc())
        ).all()
    )


def count_org_admins(db: Session, org_id: int, *, exclude_user_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.org_id == org_id,
        User.role == UserRole.user,
        User.is_active.is_(True),
        User.org_role == OrgRole.org_admin,
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return int(db.scalar(stmt) or 0)


def get_org_user(db: Session, org_id: int, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.org_id != org_id or user.role != UserRole.user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")
    return user


def new_invite_token() -> str:
    return secrets.token_urlsafe(24)


def create_org_invite(
    db: Session,
    *,
    org: Organization,
    email: str,
    invited_by: User,
    settings: Settings | None = None,
    note: str | None = None,
    app_base_url: str | None = None,
) -> Invite:
    settings = settings or get_settings()
    email_norm = email.strip().lower()
    if not email_norm or "@" not in email_norm:
        raise HTTPException(status_code=400, detail="Укажите корректный e-mail")

    existing = db.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise HTTPException(status_code=400, detail="Пользователь с этим e-mail уже есть")

    pending = db.scalar(
        select(Invite).where(
            Invite.org_id == org.id,
            Invite.email == email_norm,
            Invite.used_at.is_(None),
            Invite.expires_at > utcnow(),
        )
    )
    if pending is not None:
        raise HTTPException(status_code=400, detail="Активное приглашение на этот e-mail уже есть")

    assert_can_add_user(db, org.id)

    token = new_invite_token()
    invite = Invite(
        org_id=org.id,
        email=email_norm,
        token=token,
        expires_at=utcnow() + timedelta(hours=settings.invite_ttl_hours),
        note=note,
    )
    db.add(invite)
    db.flush()
    record_event(
        db,
        type="staff_invite_created",
        org_id=org.id,
        user_id=invited_by.id,
        details={"email": email_norm, "invite_id": invite.id},
        commit=False,
    )
    db.commit()
    db.refresh(invite)

    base = (app_base_url or settings.app_base_url or "https://app.dok.moscow").rstrip("/")
    link = f"{base}/invite/{token}"
    send_email(
        settings,
        to_addr=email_norm,
        subject=f"[Док.Москва] Приглашение в «{org.name}»",
        body=(
            f"Вас пригласили в организацию «{org.name}» на Док.Москва.\n\n"
            f"Принять приглашение: {link}\n\n"
            f"Ссылка действует {settings.invite_ttl_hours} ч.\n"
        ),
    )
    return invite


def deactivate_staff(
    db: Session,
    *,
    org: Organization,
    actor: User,
    target: User,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="Нельзя деактивировать себя")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="Сотрудник уже деактивирован")
    if is_org_admin(target) and count_org_admins(db, org.id, exclude_user_id=None) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Нельзя деактивировать единственного администратора организации",
        )

    target.is_active = False
    record_event(
        db,
        type="staff_deactivated",
        org_id=org.id,
        user_id=actor.id,
        details={"target_user_id": target.id, "email": target.email},
        commit=False,
    )
    db.commit()

    admins = [
        u
        for u in list_org_users(db, org.id)
        if u.is_active and is_org_admin(u) and u.id != target.id
    ]
    for admin in admins:
        send_email(
            settings,
            to_addr=admin.email,
            subject=f"[Док.Москва] Сотрудник деактивирован — «{org.name}»",
            body=(
                f"Администратор {actor.email} деактивировал сотрудника {target.email} "
                f"в организации «{org.name}».\n"
            ),
        )


def transfer_org_admin(
    db: Session,
    *,
    org: Organization,
    actor: User,
    target: User,
    confirm: bool,
) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="Подтвердите передачу роли администратора")
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="Вы уже администратор")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="Нельзя передать роль неактивному сотруднику")
    if not is_org_admin(actor):
        raise HTTPException(status_code=403, detail="Только администратор организации")

    target.org_role = OrgRole.org_admin
    actor.org_role = OrgRole.org_member
    record_event(
        db,
        type="staff_admin_transferred",
        org_id=org.id,
        user_id=actor.id,
        details={"to_user_id": target.id, "to_email": target.email},
        commit=False,
    )
    db.commit()
