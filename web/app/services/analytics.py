"""Аналитика и подтверждение сайта (W-40): валидация, snapshot, сохранение."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AnalyticsSettings, utcnow
from app.services.audit import record_event
from app.services.cms import purge_public_cache

log = logging.getLogger("dok.analytics")

RE_METRIKA = re.compile(r"^\d{6,10}$")
RE_GA4 = re.compile(r"^G-[A-Z0-9]{6,12}$")
RE_YANDEX_WEBMASTER = re.compile(r"^[a-f0-9]{16,64}$")
RE_GOOGLE_VERIFY = re.compile(r"^[A-Za-z0-9_-]{20,100}$")

FIELD_LABELS = {
    "yandex_metrika": "Яндекс.Метрика",
    "ga4": "Google Analytics 4",
    "yandex_webmaster": "Яндекс.Вебмастер",
    "google_site_verification": "Google Search Console",
}

_lock = threading.Lock()
_cache: "AnalyticsPublic | None" = None


class AnalyticsValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsPublic:
    """Только отвалидированные значения для публичного рендера и CSP."""

    yandex_metrika_id: str | None = None
    yandex_metrika_webvisor: bool = False
    yandex_metrika_clickmap: bool = False
    yandex_metrika_track_forms: bool = False
    ga4_measurement_id: str | None = None
    yandex_webmaster_code: str | None = None
    google_site_verification: str | None = None

    @property
    def metrika_active(self) -> bool:
        return bool(self.yandex_metrika_id)

    @property
    def ga4_active(self) -> bool:
        return bool(self.ga4_measurement_id)

    @property
    def webvisor_active(self) -> bool:
        return self.metrika_active and self.yandex_metrika_webvisor


def invalidate_analytics_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def ensure_analytics_settings(db: Session) -> AnalyticsSettings:
    row = db.get(AnalyticsSettings, 1)
    if row is None:
        row = AnalyticsSettings(id=1)
        # Миграция с env YANDEX_METRIKA_ID — один раз при создании строки
        env_id = (get_settings().yandex_metrika_id or "").strip()
        if RE_METRIKA.fullmatch(env_id):
            row.yandex_metrika_id = env_id
            row.yandex_metrika_enabled = True
            row.yandex_metrika_clickmap = True
        db.add(row)
        db.flush()
    return row


def _normalize_metrika(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "").strip())


def _normalize_ga4(raw: str) -> str:
    return (raw or "").strip().upper()


def _normalize_yandex_wm(raw: str) -> str:
    return (raw or "").strip().lower()


def _normalize_google(raw: str) -> str:
    return (raw or "").strip()


def validate_fields(
    *,
    yandex_metrika_id: str,
    yandex_metrika_enabled: bool,
    ga4_measurement_id: str,
    ga4_enabled: bool,
    yandex_webmaster_code: str,
    yandex_webmaster_enabled: bool,
    google_site_verification: str,
    google_site_verification_enabled: bool,
) -> dict[str, Any]:
    """Вернуть нормализованные поля или AnalyticsValidationError."""
    metrika = _normalize_metrika(yandex_metrika_id)
    ga4 = _normalize_ga4(ga4_measurement_id)
    ywm = _normalize_yandex_wm(yandex_webmaster_code)
    gsc = _normalize_google(google_site_verification)

    if metrika and not RE_METRIKA.fullmatch(metrika):
        raise AnalyticsValidationError(
            "Яндекс.Метрика: номер счётчика — только цифры, 6–10 знаков"
        )
    if yandex_metrika_enabled and not metrika:
        raise AnalyticsValidationError("Яндекс.Метрика: укажите номер счётчика для включения")

    if ga4 and not RE_GA4.fullmatch(ga4):
        raise AnalyticsValidationError(
            "Google Analytics 4: ожидается Measurement ID вида G-XXXXXXXX"
        )
    if ga4_enabled and not ga4:
        raise AnalyticsValidationError("GA4: укажите Measurement ID для включения")

    if ywm and not RE_YANDEX_WEBMASTER.fullmatch(ywm):
        raise AnalyticsValidationError(
            "Яндекс.Вебмастер: код подтверждения — hex 16–64 символа"
        )
    if yandex_webmaster_enabled and not ywm:
        raise AnalyticsValidationError("Яндекс.Вебмастер: укажите код для включения")

    if gsc and not RE_GOOGLE_VERIFY.fullmatch(gsc):
        raise AnalyticsValidationError(
            "Google Search Console: код — латиница/цифры/_/- , 20–100 символов"
        )
    if google_site_verification_enabled and not gsc:
        raise AnalyticsValidationError("Google Search Console: укажите код для включения")

    # Отказ при попытке XSS/разметки в «пустых» после нормализации значениях
    for label, value in (
        ("Метрика", metrika),
        ("GA4", ga4),
        ("Вебмастер", ywm),
        ("GSC", gsc),
    ):
        if value and any(ch in value for ch in "<>\"'`\\"):
            raise AnalyticsValidationError(f"{label}: недопустимые символы в идентификаторе")

    return {
        "yandex_metrika_id": metrika,
        "ga4_measurement_id": ga4,
        "yandex_webmaster_code": ywm,
        "google_site_verification": gsc,
    }


def row_to_public(row: AnalyticsSettings) -> AnalyticsPublic:
    metrika = (row.yandex_metrika_id or "").strip()
    ga4 = (row.ga4_measurement_id or "").strip().upper()
    ywm = (row.yandex_webmaster_code or "").strip().lower()
    gsc = (row.google_site_verification or "").strip()
    return AnalyticsPublic(
        yandex_metrika_id=metrika
        if row.yandex_metrika_enabled and RE_METRIKA.fullmatch(metrika)
        else None,
        yandex_metrika_webvisor=bool(row.yandex_metrika_webvisor),
        yandex_metrika_clickmap=bool(row.yandex_metrika_clickmap),
        yandex_metrika_track_forms=bool(row.yandex_metrika_track_forms),
        ga4_measurement_id=ga4 if row.ga4_enabled and RE_GA4.fullmatch(ga4) else None,
        yandex_webmaster_code=ywm
        if row.yandex_webmaster_enabled and RE_YANDEX_WEBMASTER.fullmatch(ywm)
        else None,
        google_site_verification=gsc
        if row.google_site_verification_enabled and RE_GOOGLE_VERIFY.fullmatch(gsc)
        else None,
    )


def get_analytics_public(db: Session | None = None) -> AnalyticsPublic:
    """Кэшированный snapshot для шаблонов и CSP."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
    owns = db is None
    if owns:
        from app.db import SessionLocal

        db = SessionLocal()
    try:
        row = ensure_analytics_settings(db)
        if owns:
            db.commit()
        snap = row_to_public(row)
    except Exception:
        log.exception("analytics snapshot failed")
        snap = AnalyticsPublic()
        if owns and db is not None:
            db.rollback()
    finally:
        if owns and db is not None:
            db.close()
    with _lock:
        _cache = snap
    return snap


def _toggle_diff(before: dict[str, bool], after: dict[str, bool]) -> list[str]:
    changed: list[str] = []
    for key, label in FIELD_LABELS.items():
        b, a = before.get(key), after.get(key)
        if b != a:
            changed.append(f"{label}:{'on' if a else 'off'}")
    return changed


def save_analytics_settings(
    db: Session,
    *,
    user_id: int | None,
    form: dict[str, Any],
) -> AnalyticsSettings:
    row = ensure_analytics_settings(db)
    before = {
        "yandex_metrika": bool(row.yandex_metrika_enabled),
        "ga4": bool(row.ga4_enabled),
        "yandex_webmaster": bool(row.yandex_webmaster_enabled),
        "google_site_verification": bool(row.google_site_verification_enabled),
    }

    metrika_on = str(form.get("yandex_metrika_enabled") or "") in ("1", "on", "true")
    ga4_on = str(form.get("ga4_enabled") or "") in ("1", "on", "true")
    ywm_on = str(form.get("yandex_webmaster_enabled") or "") in ("1", "on", "true")
    gsc_on = str(form.get("google_site_verification_enabled") or "") in ("1", "on", "true")

    norms = validate_fields(
        yandex_metrika_id=str(form.get("yandex_metrika_id") or ""),
        yandex_metrika_enabled=metrika_on,
        ga4_measurement_id=str(form.get("ga4_measurement_id") or ""),
        ga4_enabled=ga4_on,
        yandex_webmaster_code=str(form.get("yandex_webmaster_code") or ""),
        yandex_webmaster_enabled=ywm_on,
        google_site_verification=str(form.get("google_site_verification") or ""),
        google_site_verification_enabled=gsc_on,
    )

    row.yandex_metrika_id = norms["yandex_metrika_id"]
    row.yandex_metrika_enabled = metrika_on
    row.yandex_metrika_webvisor = str(form.get("yandex_metrika_webvisor") or "") in (
        "1",
        "on",
        "true",
    )
    row.yandex_metrika_clickmap = str(form.get("yandex_metrika_clickmap") or "") in (
        "1",
        "on",
        "true",
    )
    row.yandex_metrika_track_forms = str(form.get("yandex_metrika_track_forms") or "") in (
        "1",
        "on",
        "true",
    )
    row.ga4_measurement_id = norms["ga4_measurement_id"]
    row.ga4_enabled = ga4_on
    row.yandex_webmaster_code = norms["yandex_webmaster_code"]
    row.yandex_webmaster_enabled = ywm_on
    row.google_site_verification = norms["google_site_verification"]
    row.google_site_verification_enabled = gsc_on
    row.updated_by_user_id = user_id
    row.updated_at = utcnow()

    after = {
        "yandex_metrika": metrika_on,
        "ga4": ga4_on,
        "yandex_webmaster": ywm_on,
        "google_site_verification": gsc_on,
    }
    toggles = _toggle_diff(before, after)
    # Имена полей без значений идентификаторов
    field_names = [
        k
        for k in (
            "yandex_metrika_id",
            "yandex_metrika_enabled",
            "yandex_metrika_webvisor",
            "yandex_metrika_clickmap",
            "yandex_metrika_track_forms",
            "ga4_measurement_id",
            "ga4_enabled",
            "yandex_webmaster_code",
            "yandex_webmaster_enabled",
            "google_site_verification",
            "google_site_verification_enabled",
        )
    ]
    record_event(
        db,
        type="analytics_settings_changed",
        org_id=None,
        user_id=user_id,
        details={
            "fields": field_names,
            "toggles": toggles,
        },
        commit=False,
    )
    db.flush()
    invalidate_analytics_cache()
    purge_public_cache()
    return row
