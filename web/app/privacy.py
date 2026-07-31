"""Маскирование ПДн в списках (паспорта, адреса)."""

from __future__ import annotations

from app.models import Counterparty


def mask_passport(series: str | None, number: str | None) -> str:
    if not series and not number:
        return ""
    s = (series or "").strip()
    n = (number or "").strip()
    if len(n) >= 2:
        n_masked = "*" * max(0, len(n) - 2) + n[-2:]
    else:
        n_masked = "*" * len(n)
    return f"{s} {n_masked}".strip()


def mask_address(address: str | None) -> str:
    if not address:
        return ""
    text = address.strip()
    if len(text) <= 12:
        return "***"
    return text[:8] + "…"


def counterparty_list_item(cp: Counterparty) -> dict:
    """Карточка для списков без полных ПДн."""
    return {
        "id": cp.id,
        "org_id": cp.org_id,
        "type": cp.type.value,
        "name": cp.name,
        "fio": cp.fio,
        "inn": cp.inn,
        "phone": cp.phone,
        "email": cp.email,
        "passport": mask_passport(cp.passport_series, cp.passport_number),
        "address": mask_address(cp.address),
        "source": cp.source.value if cp.source else "manual",
    }
