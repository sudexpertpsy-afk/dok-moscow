"""Проверка доступа к «Печать и подписи» (W-43): org_admin + платный тариф."""

from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.models import TariffCode
from app.services.billing import get_tariff_limits


def can_manage_branding(db: Session, org_id: int) -> tuple[bool, str | None]:
    from app.services.billing import ensure_beta_subscriptions

    ensure_beta_subscriptions(db)
    limits = get_tariff_limits(db, org_id)
    if not limits.is_current:
        return False, "Подписка неактивна. Оплатите тариф, чтобы загружать печать и подписи."
    if limits.tariff_code not in (TariffCode.specialist, TariffCode.organization):
        return (
            False,
            f"Печать и подписи доступны на платных тарифах. Сейчас: «{limits.tariff_name}».",
        )
    return True, None


def assert_can_manage_branding(db: Session, org_id: int) -> RedirectResponse | None:
    """None если ок; иначе RedirectResponse на биллинг."""
    ok, reason = can_manage_branding(db, org_id)
    if ok:
        return None
    q = quote(reason or "Нужен платный тариф")
    return RedirectResponse(f"/cabinet/billing/?error={q}", status_code=303)
