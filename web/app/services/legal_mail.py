"""Письма администратору о мониторинге НПА."""

from __future__ import annotations

from app.config import get_settings
from app.services.mail import send_email


def _admin_addr() -> str:
    s = get_settings()
    return (s.admin_notify_email or s.bootstrap_admin_email or "").strip()


def notify_legal_change(
    *,
    act_title: str,
    act_slug: str,
    change_summary: str,
    draft_version_id: int | None,
    eo_numbers: list[str],
) -> bool:
    settings = get_settings()
    to_addr = _admin_addr()
    eos = ", ".join(eo_numbers) if eo_numbers else "—"
    body = (
        f"Обнаружено изменение по акту:\n"
        f"  {act_title}\n"
        f"  slug: {act_slug}\n"
        f"  eoNumber: {eos}\n"
        f"  черновик редакции id: {draft_version_id or '—'}\n\n"
        f"{change_summary}\n\n"
        f"Подтвердите публикацию в админке (W-19).\n"
        f"{settings.app_base_url.rstrip('/')}/admin/\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[Док.Москва] Изменение НПА: {act_title[:80]}",
        body=body,
    )


def notify_legal_source_errors(
    *,
    act_title: str,
    act_slug: str,
    error_count: int,
    last_detail: str,
) -> bool:
    settings = get_settings()
    to_addr = _admin_addr()
    body = (
        f"Три ошибки подряд при мониторинге акта:\n"
        f"  {act_title}\n"
        f"  slug: {act_slug}\n"
        f"  ошибок подряд: {error_count}\n"
        f"  последняя: {last_detail}\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[Док.Москва] Ошибка источника НПА: {act_slug}",
        body=body,
    )
