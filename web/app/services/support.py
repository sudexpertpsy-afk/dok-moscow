"""Обращения в поддержку и предложения улучшений."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.models import (
    SupportTicket,
    SupportTicketKind,
    SupportTicketStatus,
    utcnow,
)
from app.services.audit import record_event
from app.services.branding import MAX_UPLOAD_BYTES, BrandingError, _open_verified
from app.services.mail import send_email
from app.services.rate_counters import get_count, incr_counter, set_if_absent
from app.services.safe_paths import org_files_root, resolve_under, resolve_under_org

SUBJECT_MAX = 200
BODY_MAX = 5000
RATE_LIMIT = 5
PAGE_URL_MAX = 1024
UA_MAX = 512
VERSION_MAX = 64

KIND_LABELS: dict[str, str] = {
    SupportTicketKind.support.value: "Поддержка",
    SupportTicketKind.improvement.value: "Улучшение",
}

STATUS_LABELS: dict[str, str] = {
    SupportTicketStatus.new.value: "новое",
    SupportTicketStatus.in_progress.value: "в работе",
    SupportTicketStatus.done.value: "закрыто",
    SupportTicketStatus.rejected.value: "отклонено",
}

STATUS_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "Все"),
    ("new", "Новые"),
    ("in_progress", "В работе"),
    ("done", "Закрытые"),
    ("rejected", "Отклонённые"),
)

KIND_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "Все"),
    ("support", "Поддержка"),
    ("improvement", "Улучшения"),
)


class SupportError(ValueError):
    """Ошибка валидации обращения."""


class SupportRateLimitError(SupportError):
    """Превышен лимит обращений."""


def _hour_bucket_key(user_id: int) -> str:
    hour = utcnow().strftime("%Y%m%d%H")
    return f"support:user:{int(user_id)}:{hour}"


def _hour_window_start():
    now = utcnow()
    return now.replace(minute=0, second=0, microsecond=0)


def check_rate_limit(db: Session, user_id: int) -> None:
    key = _hour_bucket_key(user_id)
    set_if_absent(db, key, window_start=_hour_window_start(), count=0)
    if get_count(db, key) >= RATE_LIMIT:
        raise SupportRateLimitError(
            "Слишком много обращений. Подождите час или напишите на e-mail поддержки."
        )


def consume_rate_limit(db: Session, user_id: int) -> None:
    key = _hour_bucket_key(user_id)
    set_if_absent(db, key, window_start=_hour_window_start(), count=0)
    n = incr_counter(db, key, window_start=_hour_window_start(), by=1)
    if n > RATE_LIMIT:
        raise SupportRateLimitError(
            "Слишком много обращений. Подождите час или напишите на e-mail поддержки."
        )


def parse_kind(raw: str | None) -> SupportTicketKind:
    val = (raw or "").strip().lower()
    try:
        return SupportTicketKind(val)
    except ValueError as exc:
        raise SupportError("Некорректный тип обращения.") from exc


def parse_status(raw: str | None) -> SupportTicketStatus | None:
    val = (raw or "").strip().lower()
    if not val:
        return None
    try:
        return SupportTicketStatus(val)
    except ValueError as exc:
        raise SupportError("Некорректный статус.") from exc


def normalize_subject(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        raise SupportError("Укажите тему.")
    if len(s) > SUBJECT_MAX:
        raise SupportError(f"Тема не длиннее {SUBJECT_MAX} символов.")
    return s


def normalize_body(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        raise SupportError("Опишите обращение.")
    if len(s) > BODY_MAX:
        raise SupportError(f"Текст не длиннее {BODY_MAX} символов.")
    return s


def _clip(raw: str | None, max_len: int) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    return s[:max_len]


def support_dir(org_id: int) -> Path:
    root = resolve_under(org_files_root(org_id), "support")
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_attachment(org_id: int, ticket_id: int, data: bytes, filename: str | None) -> str:
    """Сохранить одно изображение; вернуть относительный путь {org_id}/support/..."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise SupportError(f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ.")
    try:
        img = _open_verified(data)
    except BrandingError as exc:
        raise SupportError(str(exc)) from exc
    ext = ".png"
    name = (filename or "").lower()
    if name.endswith((".jpg", ".jpeg")) or img.format == "JPEG":
        ext = ".jpg"
    safe_name = f"{ticket_id}_{uuid.uuid4().hex[:12]}{ext}"
    path = resolve_under(support_dir(org_id), safe_name)
    path.write_bytes(data)
    return f"{int(org_id)}/support/{safe_name}"


def resolve_attachment(org_id: int, ticket: SupportTicket) -> Path:
    if not ticket.attachment_path:
        raise FileNotFoundError("Нет вложения")
    return resolve_under_org(org_id, ticket.attachment_path)


def create_ticket(
    db: Session,
    *,
    org_id: int,
    user_id: int,
    kind: SupportTicketKind | str,
    subject: str,
    body: str,
    page_url: str | None = None,
    app_version: str | None = None,
    user_agent: str | None = None,
    attachment_bytes: bytes | None = None,
    attachment_filename: str | None = None,
) -> SupportTicket:
    kind_e = kind if isinstance(kind, SupportTicketKind) else parse_kind(str(kind))
    subject_n = normalize_subject(subject)
    body_n = normalize_body(body)
    check_rate_limit(db, user_id)
    ticket = SupportTicket(
        org_id=org_id,
        user_id=user_id,
        kind=kind_e,
        subject=subject_n,
        body=body_n,
        status=SupportTicketStatus.new,
        page_url=_clip(page_url, PAGE_URL_MAX),
        app_version=_clip(app_version, VERSION_MAX),
        user_agent=_clip(user_agent, UA_MAX),
    )
    db.add(ticket)
    db.flush()
    if attachment_bytes:
        ticket.attachment_path = save_attachment(
            org_id, ticket.id, attachment_bytes, attachment_filename
        )
    consume_rate_limit(db, user_id)
    record_event(
        db,
        type="support.ticket_created",
        org_id=org_id,
        user_id=user_id,
        details={"ticket_id": ticket.id, "kind": kind_e.value},
        commit=False,
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def list_for_org(
    db: Session,
    org_id: int,
    *,
    kind: SupportTicketKind | None = None,
    limit: int = 100,
) -> list[SupportTicket]:
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.org_id == org_id)
        .order_by(SupportTicket.id.desc())
        .limit(limit)
    )
    if kind is not None:
        stmt = stmt.where(SupportTicket.kind == kind)
    return list(db.scalars(stmt).all())


def get_for_org(db: Session, org_id: int, ticket_id: int) -> SupportTicket | None:
    return db.scalar(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.org_id == org_id,
        )
    )


def list_admin(
    db: Session,
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[SupportTicket]:
    stmt = (
        select(SupportTicket)
        .options(
            joinedload(SupportTicket.organization),
            joinedload(SupportTicket.user),
        )
        .order_by(SupportTicket.id.desc())
        .limit(limit)
    )
    kind_e = parse_kind(kind) if kind else None
    status_e = parse_status(status) if status else None
    if kind_e is not None:
        stmt = stmt.where(SupportTicket.kind == kind_e)
    if status_e is not None:
        stmt = stmt.where(SupportTicket.status == status_e)
    return list(db.scalars(stmt).unique().all())


def get_admin(db: Session, ticket_id: int) -> SupportTicket | None:
    return db.scalar(
        select(SupportTicket)
        .options(
            joinedload(SupportTicket.organization),
            joinedload(SupportTicket.user),
        )
        .where(SupportTicket.id == ticket_id)
    )


def count_new(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .where(SupportTicket.status == SupportTicketStatus.new)
        )
        or 0
    )


def update_ticket_admin(
    db: Session,
    ticket: SupportTicket,
    *,
    status: SupportTicketStatus | str,
    admin_note: str | None,
    admin_reply: str | None,
    actor_user_id: int | None,
) -> tuple[SupportTicket, bool]:
    """Обновить тикет. Возвращает (ticket, status_changed)."""
    status_e = status if isinstance(status, SupportTicketStatus) else parse_status(status)
    if status_e is None:
        raise SupportError("Укажите статус.")
    status_changed = ticket.status != status_e
    ticket.status = status_e
    ticket.admin_note = (admin_note or "").strip() or None
    ticket.admin_reply = (admin_reply or "").strip() or None
    ticket.updated_at = utcnow()
    record_event(
        db,
        type="support.ticket_updated",
        org_id=ticket.org_id,
        user_id=actor_user_id,
        details={
            "ticket_id": ticket.id,
            "status": status_e.value,
            "status_changed": status_changed,
        },
        commit=False,
    )
    db.commit()
    db.refresh(ticket)
    return ticket, status_changed


def ticket_admin_url(settings: Settings, ticket_id: int) -> str:
    base = (settings.app_base_url or "https://app.dok.moscow").rstrip("/")
    return f"{base}/admin/support/{int(ticket_id)}"


def ticket_cabinet_url(settings: Settings, ticket_id: int) -> str:
    base = (settings.app_base_url or "https://app.dok.moscow").rstrip("/")
    return f"{base}/cabinet/support/{int(ticket_id)}"


def notify_admin_new_ticket(settings: Settings, ticket: SupportTicket) -> bool:
    to_addr = (settings.admin_notify_email or settings.bootstrap_admin_email or "").strip()
    if not to_addr:
        return False
    kind_l = KIND_LABELS.get(ticket.kind.value, ticket.kind.value)
    link = ticket_admin_url(settings, ticket.id)
    subject = f"[Док.Москва] {kind_l}: {ticket.subject[:80]}"
    body = (
        f"Новое обращение ({kind_l}).\n\n"
        f"Тема: {ticket.subject}\n"
        f"Организация ID: {ticket.org_id}\n"
        f"Автор ID: {ticket.user_id or '—'}\n"
        f"Страница: {ticket.page_url or '—'}\n"
        f"Версия: {ticket.app_version or '—'}\n"
        f"ID: {ticket.id}\n\n"
        f"Открыть: {link}\n"
    )
    return send_email(settings, to_addr=to_addr, subject=subject, body=body)


def notify_user_status_change(
    settings: Settings,
    ticket: SupportTicket,
    *,
    user_email: str | None,
) -> bool:
    to_addr = (user_email or "").strip()
    if not to_addr:
        return False
    status_l = STATUS_LABELS.get(ticket.status.value, ticket.status.value)
    kind_l = KIND_LABELS.get(ticket.kind.value, ticket.kind.value)
    link = ticket_cabinet_url(settings, ticket.id)
    reply = (ticket.admin_reply or "").strip()
    reply_block = f"\nОтвет поддержки:\n{reply}\n" if reply else "\n"
    body = (
        f"Здравствуйте!\n\n"
        f"Статус обращения «{ticket.subject}» ({kind_l}) изменён: {status_l}."
        f"{reply_block}\n"
        f"Открыть обращение: {link}\n\n"
        f"— Команда Док.Москва\n"
    )
    subject = f"[Док.Москва] Обращение #{ticket.id}: {status_l}"
    return send_email(settings, to_addr=to_addr, subject=subject, body=body)


@dataclass
class TicketAuthor:
    email: str | None
    org_name: str | None


def author_info(ticket: SupportTicket) -> TicketAuthor:
    email = ticket.user.email if ticket.user is not None else None
    org_name = ticket.organization.name if ticket.organization is not None else None
    return TicketAuthor(email=email, org_name=org_name)


def default_app_version(settings: Settings | None = None) -> str:
    return (settings or get_settings()).app_version
