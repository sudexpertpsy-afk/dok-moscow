"""Заявки с лендинга: дедуп, воронка W-39, уведомления."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.defaults import empty_requisites
from app.models import (
    Invite,
    Lead,
    LeadStatus,
    Organization,
    User,
    utcnow,
)
from app.security import new_invite_token
from app.services.admin_subscription import AdminSubscriptionError, apply_admin_subscription
from app.services.audit import record_event
from app.services.billing import ensure_payment_settings, ensure_tariffs
from app.services.mail import send_email

STATUS_LABELS: dict[str, str] = {
    LeadStatus.new.value: "новая",
    LeadStatus.invited.value: "приглашение отправлено",
    LeadStatus.registered.value: "зарегистрирован",
    LeadStatus.rejected.value: "отклонена",
    LeadStatus.spam.value: "спам",
}

STATUS_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "Все"),
    ("new", "Новые"),
    ("invited", "Приглашение отправлено"),
    ("registered", "Зарегистрирован"),
    ("rejected", "Отклонена"),
    ("spam", "Спам"),
)

BETA_MONTHS = (1, 3, 6, 12)

REJECT_EMAIL_BODY = (
    "Здравствуйте!\n\n"
    "Спасибо за интерес к Док.Москва. Сейчас доступ ограничен — "
    "мы оставили вас в списке ожидания и свяжемся, когда откроем места.\n\n"
    "— Команда Док.Москва\n"
)


class LeadError(ValueError):
    """Ошибка обработки заявки."""


@dataclass
class LeadView:
    lead: Lead
    existing_user: User | None
    existing_org_name: str | None
    open_invite: Invite | None


@dataclass
class CreateOrgInviteResult:
    lead: Lead
    org: Organization
    invite: Invite
    invite_link: str
    email_sent: bool


def default_org_name(lead: Lead) -> str:
    """Имя по умолчанию — пользователь уточнит при онбординге."""
    return f"Организация {lead.email}"


def beta_defaults(db: Session) -> tuple[str, int]:
    ensure_payment_settings(db)
    row = ensure_payment_settings(db)
    tariff = (row.beta_default_tariff or "organization").strip() or "organization"
    months = int(row.beta_default_months or 3)
    if months not in BETA_MONTHS:
        months = 3
    return tariff, months


def count_new_leads(db: Session) -> int:
    from sqlalchemy import func

    return int(
        db.scalar(select(func.count()).select_from(Lead).where(Lead.status == LeadStatus.new))
        or 0
    )


def lead_admin_url(settings: Settings, lead_id: int) -> str:
    base = (settings.app_base_url or "https://app.dok.moscow").rstrip("/")
    return f"{base}/admin/leads#lead-{lead_id}"


def create_lead(
    db: Session,
    *,
    email: str,
    profile: str | None,
    comment: str | None,
) -> tuple[Lead, bool]:
    """Создать или поднять заявку. Возвращает (lead, is_new)."""
    email_n = email.strip().lower()
    profile_n = (profile or "").strip()[:255] or None
    comment_n = (comment or "").strip() or None
    now = utcnow()

    existing = db.scalar(select(Lead).where(Lead.email == email_n).order_by(Lead.id.desc()))
    if existing is not None:
        existing.contact_count = int(existing.contact_count or 1) + 1
        if profile_n:
            existing.profile = profile_n
        if comment_n:
            stamp = now.strftime("%d.%m.%Y %H:%M")
            prev = (existing.comment or "").strip()
            block = f"[{stamp}] {comment_n}"
            existing.comment = f"{prev}\n{block}".strip() if prev else block
        if existing.status in (LeadStatus.rejected, LeadStatus.spam):
            existing.status = LeadStatus.new
        existing.updated_at = now
        record_event(
            db,
            type="lead_bumped",
            org_id=existing.org_id,
            user_id=None,
            details={
                "lead_id": existing.id,
                "email": existing.email,
                "contact_count": existing.contact_count,
                "status": existing.status.value,
            },
            commit=False,
        )
        db.commit()
        db.refresh(existing)
        return existing, False

    lead = Lead(
        email=email_n,
        profile=profile_n,
        comment=comment_n,
        status=LeadStatus.new,
        contact_count=1,
        updated_at=now,
    )
    db.add(lead)
    db.flush()
    record_event(
        db,
        type="lead_created",
        org_id=None,
        user_id=None,
        details={"lead_id": lead.id, "email": lead.email, "profile": lead.profile},
        commit=False,
    )
    db.commit()
    db.refresh(lead)
    return lead, True


def notify_admin_new_lead(settings: Settings, lead: Lead, db: Session | None = None) -> bool:
    """Письмо владельцу о новой заявке — только если ещё не отправляли."""
    if lead.admin_notified_at is not None:
        return False
    to_addr = (settings.admin_notify_email or settings.bootstrap_admin_email or "").strip()
    link = lead_admin_url(settings, lead.id)
    subject = f"[Док.Москва] Заявка на ранний доступ: {lead.email}"
    body = (
        f"Новая заявка с лендинга.\n\n"
        f"E-mail: {lead.email}\n"
        f"Профиль: {lead.profile or '—'}\n"
        f"Комментарий: {lead.comment or '—'}\n"
        f"ID: {lead.id}\n"
        f"Время: {lead.ts}\n\n"
        f"Открыть заявку: {link}\n"
    )
    ok = send_email(settings, to_addr=to_addr, subject=subject, body=body)
    lead.admin_notified_at = utcnow()
    if db is not None:
        db.commit()
    return ok


def find_open_invite(db: Session, email: str) -> Invite | None:
    email_n = email.strip().lower()
    now = utcnow()
    return db.scalar(
        select(Invite)
        .options(joinedload(Invite.organization))
        .where(
            Invite.email == email_n,
            Invite.used_at.is_(None),
            Invite.expires_at > now,
        )
        .order_by(Invite.id.desc())
        .limit(1)
    )


def lead_view(db: Session, lead: Lead) -> LeadView:
    email_n = lead.email.strip().lower()
    user = db.scalar(
        select(User).options(joinedload(User.organization)).where(User.email == email_n)
    )
    org_name = None
    if user is not None and user.organization is not None:
        org_name = user.organization.name
    elif user is not None and user.org_id:
        org = db.get(Organization, user.org_id)
        org_name = org.name if org else None
    open_inv = find_open_invite(db, email_n)
    # авто-синхронизация: пользователь уже есть → registered
    if user is not None and lead.status not in (LeadStatus.spam, LeadStatus.rejected):
        if lead.status != LeadStatus.registered:
            lead.status = LeadStatus.registered
            if user.org_id and lead.org_id is None:
                lead.org_id = user.org_id
            lead.updated_at = utcnow()
            db.flush()
    return LeadView(
        lead=lead,
        existing_user=user,
        existing_org_name=org_name,
        open_invite=open_inv,
    )


def _invite_link(settings: Settings, token: str) -> str:
    base = (settings.app_base_url or "https://app.dok.moscow").rstrip("/")
    return f"{base}/invite/{token}"


def _send_invite_email(
    settings: Settings, *, org: Organization, email: str, token: str
) -> bool:
    link = _invite_link(settings, token)
    return send_email(
        settings,
        to_addr=email,
        subject=f"[Док.Москва] Приглашение в «{org.name}»",
        body=(
            f"Вас пригласили в организацию «{org.name}» на Док.Москва.\n\n"
            f"Принять приглашение: {link}\n\n"
            f"Ссылка действует {settings.invite_ttl_hours} ч.\n"
        ),
    )


def _kill_invite(invite: Invite) -> None:
    invite.expires_at = utcnow() - timedelta(seconds=1)


def _create_invite(
    db: Session,
    *,
    org: Organization,
    lead: Lead,
    settings: Settings,
) -> Invite:
    token = new_invite_token()
    invite = Invite(
        org_id=org.id,
        email=lead.email.strip().lower(),
        token=token,
        expires_at=utcnow() + timedelta(hours=settings.invite_ttl_hours),
        lead_id=lead.id,
        note=f"lead:{lead.id}",
    )
    db.add(invite)
    db.flush()
    return invite


def create_org_and_invite(
    db: Session,
    *,
    lead: Lead,
    actor: User,
    org_name: str,
    tariff_code: str | None = None,
    months: int | None = None,
    send_email_now: bool = True,
    settings: Settings | None = None,
) -> CreateOrgInviteResult:
    """Организация + подписка (бета, W-38) + инвайт (+ письмо) одним действием."""
    settings = settings or get_settings()
    if lead.status == LeadStatus.spam:
        raise LeadError("Заявка помечена как спам")
    email_n = lead.email.strip().lower()
    if db.scalar(select(User).where(User.email == email_n)):
        raise LeadError(f"Пользователь {email_n} уже зарегистрирован")

    name = (org_name or "").strip() or default_org_name(lead)
    if len(name) > 255:
        name = name[:255]
    default_tariff, default_months = beta_defaults(db)
    code = (tariff_code or default_tariff).strip()
    m = int(months if months is not None else default_months)
    if m not in BETA_MONTHS:
        raise LeadError("Срок подписки: +1 / +3 / +6 / +12 месяцев")

    ensure_tariffs(db)
    org = Organization(
        name=name,
        requisites=empty_requisites(),
        source_lead_id=lead.id,
    )
    db.add(org)
    db.flush()

    try:
        apply_admin_subscription(
            db,
            org=org,
            actor=actor,
            action="apply",
            tariff_code=code,
            term_mode="relative",
            months=m,
            reason="beta",
            reason_comment=f"бета из заявки #{lead.id}",
            notify=False,
            confirm="YES",
            totp_ok=False,
        )
    except AdminSubscriptionError as exc:
        raise LeadError(str(exc)) from exc

    invite = _create_invite(db, org=org, lead=lead, settings=settings)
    lead.org_id = org.id
    lead.invite_id = invite.id
    lead.status = LeadStatus.invited
    lead.updated_at = utcnow()

    email_sent = False
    if send_email_now:
        email_sent = _send_invite_email(
            settings, org=org, email=email_n, token=invite.token
        )

    record_event(
        db,
        type="lead_invited",
        org_id=org.id,
        user_id=actor.id,
        details={
            "lead_id": lead.id,
            "email": email_n,
            "invite_id": invite.id,
            "org_name": org.name,
            "tariff": code,
            "months": m,
            "email_sent": email_sent,
            "mode": "create_org",
        },
        commit=False,
    )
    db.commit()
    db.refresh(lead)
    db.refresh(org)
    db.refresh(invite)
    return CreateOrgInviteResult(
        lead=lead,
        org=org,
        invite=invite,
        invite_link=_invite_link(settings, invite.token),
        email_sent=email_sent,
    )


def invite_to_existing_org(
    db: Session,
    *,
    lead: Lead,
    actor: User,
    org: Organization,
    send_email_now: bool = True,
    settings: Settings | None = None,
) -> CreateOrgInviteResult:
    """Инвайт в уже существующую организацию (второй пользователь)."""
    settings = settings or get_settings()
    email_n = lead.email.strip().lower()
    if db.scalar(select(User).where(User.email == email_n)):
        raise LeadError(f"Пользователь {email_n} уже зарегистрирован")

    open_inv = find_open_invite(db, email_n)
    if open_inv is not None:
        raise LeadError(
            f"Инвайт уже отправлен {open_inv.created_at.strftime('%d.%m.%Y') if open_inv.created_at else ''}".strip()
        )

    invite = _create_invite(db, org=org, lead=lead, settings=settings)
    lead.org_id = org.id
    lead.invite_id = invite.id
    lead.status = LeadStatus.invited
    lead.updated_at = utcnow()

    email_sent = False
    if send_email_now:
        email_sent = _send_invite_email(
            settings, org=org, email=email_n, token=invite.token
        )

    record_event(
        db,
        type="lead_invited",
        org_id=org.id,
        user_id=actor.id,
        details={
            "lead_id": lead.id,
            "email": email_n,
            "invite_id": invite.id,
            "org_name": org.name,
            "email_sent": email_sent,
            "mode": "existing_org",
        },
        commit=False,
    )
    db.commit()
    db.refresh(lead)
    db.refresh(invite)
    return CreateOrgInviteResult(
        lead=lead,
        org=org,
        invite=invite,
        invite_link=_invite_link(settings, invite.token),
        email_sent=email_sent,
    )


def resend_invite(
    db: Session,
    *,
    lead: Lead,
    actor: User,
    settings: Settings | None = None,
) -> CreateOrgInviteResult:
    """Повторный инвайт: старый токен гасится."""
    settings = settings or get_settings()
    email_n = lead.email.strip().lower()
    if db.scalar(select(User).where(User.email == email_n)):
        raise LeadError(f"Пользователь {email_n} уже зарегистрирован")

    org_id = lead.org_id
    open_inv = find_open_invite(db, email_n)
    if open_inv is not None:
        org_id = org_id or open_inv.org_id
        _kill_invite(open_inv)
    if lead.invite_id:
        old = db.get(Invite, lead.invite_id)
        if old is not None and old.used_at is None and not old.is_expired():
            _kill_invite(old)
            org_id = org_id or old.org_id

    if org_id is None:
        raise LeadError("Нет организации для повторного инвайта — создайте организацию")
    org = db.get(Organization, org_id)
    if org is None:
        raise LeadError("Организация не найдена")

    # погасить любые другие открытые по e-mail
    now = utcnow()
    for inv in db.scalars(
        select(Invite).where(
            Invite.email == email_n,
            Invite.used_at.is_(None),
            Invite.expires_at > now,
        )
    ).all():
        _kill_invite(inv)
    db.flush()

    invite = _create_invite(db, org=org, lead=lead, settings=settings)
    lead.org_id = org.id
    lead.invite_id = invite.id
    lead.status = LeadStatus.invited
    lead.updated_at = now

    email_sent = _send_invite_email(
        settings, org=org, email=email_n, token=invite.token
    )
    record_event(
        db,
        type="lead_invite_resent",
        org_id=org.id,
        user_id=actor.id,
        details={
            "lead_id": lead.id,
            "email": email_n,
            "invite_id": invite.id,
            "email_sent": email_sent,
        },
        commit=False,
    )
    db.commit()
    db.refresh(lead)
    db.refresh(invite)
    return CreateOrgInviteResult(
        lead=lead,
        org=org,
        invite=invite,
        invite_link=_invite_link(settings, invite.token),
        email_sent=email_sent,
    )


def reject_lead(
    db: Session,
    *,
    lead: Lead,
    actor: User,
    send_mail: bool = False,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    lead.status = LeadStatus.rejected
    lead.updated_at = utcnow()
    emailed = False
    if send_mail:
        emailed = send_email(
            settings,
            to_addr=lead.email,
            subject="[Док.Москва] Заявка на доступ",
            body=REJECT_EMAIL_BODY,
        )
    record_event(
        db,
        type="lead_rejected",
        org_id=lead.org_id,
        user_id=actor.id,
        details={
            "lead_id": lead.id,
            "email": lead.email,
            "email_sent": emailed,
        },
        commit=False,
    )
    db.commit()
    return emailed


def mark_spam(
    db: Session,
    *,
    lead: Lead,
    actor: User,
) -> None:
    lead.status = LeadStatus.spam
    lead.updated_at = utcnow()
    record_event(
        db,
        type="lead_spam",
        org_id=lead.org_id,
        user_id=actor.id,
        details={"lead_id": lead.id, "email": lead.email},
        commit=False,
    )
    db.commit()


def save_admin_note(
    db: Session,
    *,
    lead: Lead,
    actor: User,
    note: str,
) -> None:
    lead.admin_note = (note or "").strip() or None
    lead.updated_at = utcnow()
    record_event(
        db,
        type="lead_note",
        org_id=lead.org_id,
        user_id=actor.id,
        details={"lead_id": lead.id, "note": lead.admin_note or ""},
        commit=False,
    )
    db.commit()


def mark_lead_registered_from_invite(db: Session, invite: Invite) -> None:
    """Вызывается при принятии инвайта — статус «зарегистрирован»."""
    lead: Lead | None = None
    if invite.lead_id:
        lead = db.get(Lead, invite.lead_id)
    if lead is None:
        lead = db.scalar(
            select(Lead)
            .where(Lead.email == invite.email.strip().lower())
            .order_by(Lead.id.desc())
        )
    if lead is None:
        return
    lead.status = LeadStatus.registered
    lead.org_id = invite.org_id
    lead.invite_id = invite.id
    lead.updated_at = utcnow()
    record_event(
        db,
        type="lead_registered",
        org_id=invite.org_id,
        user_id=None,
        details={
            "lead_id": lead.id,
            "email": lead.email,
            "invite_id": invite.id,
        },
        commit=False,
    )
