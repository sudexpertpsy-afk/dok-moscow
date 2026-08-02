"""Письма биллинга: оплата, окончание подписки, автосписание, ручное продление (W-14)."""

from __future__ import annotations

from datetime import datetime

from app.config import Settings, get_settings
from app.models import Organization, Payment, Subscription
from app.services.mail import send_email


def _rub(amount_kop: int) -> str:
    rub = amount_kop // 100
    kop = amount_kop % 100
    return f"{rub:,}".replace(",", "\u00a0") + f",{kop:02d} ₽"


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        return value.strftime("%d.%m.%Y %H:%M UTC")
    return value.strftime("%d.%m.%Y %H:%M %Z")


def notify_payment_success(
    *,
    to_addr: str,
    org: Organization,
    payment: Payment,
    ends_at: datetime | None,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    receipt_line = ""
    if payment.receipt_url:
        receipt_line = f"Чек: {payment.receipt_url}\n"
    elif payment.receipt_status:
        receipt_line = f"Статус чека: {payment.receipt_status}\n"
    body = (
        f"Оплата подписки «{settings.app_name}» прошла успешно.\n\n"
        f"Организация: {org.name}\n"
        f"Назначение: {payment.purpose}\n"
        f"Сумма: {_rub(payment.amount_kop)}\n"
        f"Подписка действует до: {_fmt_dt(ends_at)}\n"
        f"{receipt_line}"
        f"\nКабинет: {settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[{settings.app_name}] Оплата получена",
        body=body,
    )


def notify_subscription_expiring(
    *,
    to_addr: str,
    org: Organization,
    sub: Subscription,
    days_left: int,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    tariff_name = sub.tariff.name if sub.tariff else "текущий"
    body = (
        f"Подписка организации «{org.name}» на тариф «{tariff_name}» "
        f"заканчивается через {days_left} "
        f"{'день' if days_left == 1 else 'дня' if days_left in (2, 3, 4) else 'дней'}.\n"
        f"Дата окончания: {_fmt_dt(sub.ends_at)}\n\n"
        f"Автопродление выключено. Чтобы продолжить работу без ограничений генерации, "
        f"оплатите следующий период в кабинете:\n"
        f"{settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[{settings.app_name}] Подписка заканчивается через {days_left} дн.",
        body=body,
    )


def notify_autorenew_failed(
    *,
    to_addr: str,
    org: Organization,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    body = (
        f"Автоматическое списание для «{org.name}» не удалось после нескольких попыток.\n"
        f"Продлите подписку вручную в кабинете:\n"
        f"{settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[{settings.app_name}] Не удалось продлить подписку",
        body=body,
    )


def notify_manual_extend(
    *,
    to_addr: str,
    org: Organization,
    payment: Payment,
    ends_at: datetime | None,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    body = (
        f"Подписка организации «{org.name}» продлена администратором сервиса.\n\n"
        f"Основание: {payment.manual_basis or '—'}\n"
        f"Назначение: {payment.purpose}\n"
        f"Сумма учёта: {_rub(payment.amount_kop)}\n"
        f"Подписка действует до: {_fmt_dt(ends_at)}\n\n"
        f"Кабинет: {settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[{settings.app_name}] Подписка продлена",
        body=body,
    )


def notify_admin_subscription_change(
    *,
    to_addr: str,
    org: Organization,
    tariff_name: str,
    ends_at: datetime | None,
    terminated: bool = False,
    settings: Settings | None = None,
) -> bool:
    """W-38: письмо организации о тарифе и сроке (без суммы)."""
    settings = settings or get_settings()
    if terminated:
        body = (
            f"Подписка организации «{org.name}» завершена администратором сервиса.\n\n"
            f"Тариф на момент завершения: {tariff_name}\n"
            f"Доступ к платным функциям ограничен.\n\n"
            f"Кабинет: {settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
        )
        subject = f"[{settings.app_name}] Подписка завершена"
    else:
        body = (
            f"Подписка организации «{org.name}» обновлена администратором сервиса.\n\n"
            f"Тариф: {tariff_name}\n"
            f"Действует до: {_fmt_dt(ends_at)}\n\n"
            f"Кабинет: {settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
        )
        subject = f"[{settings.app_name}] Подписка обновлена"
    return send_email(settings, to_addr=to_addr, subject=subject, body=body)
