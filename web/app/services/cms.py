"""W-36: публичная CMS, тарифы, промокоды и анонсы."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.http_cache import purge_public_cache as _purge_public_cache
from app.models import (
    Announcement,
    ContentBlock,
    ContentBlockVersion,
    Payment,
    PromoCode,
    PromoCodeType,
    SubscriptionPeriod,
    Tariff,
    TariffCode,
    TariffPriceLog,
    utcnow,
)
from app.services.audit import record_event
from app.services.billing import ensure_tariffs

CONTENT_SLOT_KEYS: tuple[str, ...] = (
    "hero_headline",
    "hero_sub",
    "feature_1",
    "feature_2",
    "feature_3",
    "tariffs_note",
    "faq",
    "contacts",
    "footer_text",
    # W-44 волна 2 — сегменты, доверие, лента релизов (Markdown)
    "segment_ekspertov",
    "segment_organizatsiy",
    "segment_uchebnykh",
    "bezopasnost",
    "novoe",
    # W-44 волна 3 — отзывы и код акции запуска
    "review_1_name",
    "review_1_role",
    "review_1_text",
    "review_2_name",
    "review_2_role",
    "review_2_text",
    "review_3_name",
    "review_3_role",
    "review_3_text",
    "launch_promo_code",
)
INLINE_SLOTS = {
    "hero_headline",
    "hero_sub",
    "feature_1",
    "feature_2",
    "feature_3",
    "tariffs_note",
    "footer_text",
    "review_1_name",
    "review_1_role",
    "review_2_name",
    "review_2_role",
    "review_3_name",
    "review_3_role",
    "launch_promo_code",
}

# Промокод акции «первые N — 50%»: создаётся в Едином окне; слот launch_promo_code может переопределить.
DEFAULT_LAUNCH_PROMO_CODE = "BETA50"

DEFAULT_TARIFF_META: dict[TariffCode, dict[str, object]] = {
    TariffCode.guest: {
        "blurb": "До 3 документов в месяц, водяной знак на PDF. Чтобы оценить кабинет без оплаты.",
        "features": ["до 3 документов в месяц", "1 пользователь", "водяной знак на PDF"],
    },
    TariffCode.specialist: {
        "blurb": "Один пользователь, полный кабинет без водяного знака.",
        "features": [
            "полный кабинет без водяного знака",
            "1 пользователь",
            "журналы, шаблоны, DaData",
            "Импорт контрагентов из Excel",
            "Выгрузка всех данных одним архивом",
            "Печать и подпись в PDF (факсимиле)",
        ],
    },
    TariffCode.organization: {
        "blurb": "До 5 пользователей, свои шаблоны.",
        "features": [
            "до 5 пользователей по приглашениям",
            "свои шаблоны организации",
            "Импорт контрагентов из Excel",
            "Выгрузка всех данных одним архивом",
            "Печать и подпись в PDF (факсимиле)",
            "приоритет для команд СРО и учреждений",
        ],
    },
}

_LINK_RE = re.compile(r"\[([^\]]{1,300})\]\(([^)\s]{1,1000})\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def purge_public_cache() -> None:
    _purge_public_cache()


def _safe_href(raw: str) -> str | None:
    href = html.unescape(raw.strip())
    low = href.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return href
    if href.startswith("/") and not href.startswith("//"):
        return href
    if href.startswith("#"):
        return href
    return None


def _render_inline(text: str) -> str:
    escaped = html.escape(text, quote=True)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)

    def repl(match: re.Match[str]) -> str:
        label = match.group(1)
        href = _safe_href(match.group(2))
        if href is None:
            return label
        return f'<a href="{html.escape(href, quote=True)}" rel="nofollow noopener">{label}</a>'

    return _LINK_RE.sub(repl, escaped)


def safe_markdown(body_md: str | None, *, inline: bool = False) -> Markup:
    """Минимальный безопасный Markdown: paragraphs, ul, links, bold, italic.

    Сырой HTML всегда экранируется до обработки markdown-разметки.
    """
    raw = (body_md or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return Markup("")
    if inline:
        line = " ".join(part.strip() for part in raw.splitlines() if part.strip())
        return Markup(_render_inline(line))

    parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                parts.append(f"<p>{_render_inline(text)}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            items = "".join(f"<li>{item}</li>" for item in list_items)
            parts.append(f"<ul>{items}</ul>")
            list_items.clear()

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h3>{_render_inline(stripped[4:].strip())}</h3>")
        elif stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h2>{_render_inline(stripped[3:].strip())}</h2>")
        elif stripped.startswith(("- ", "* ")):
            flush_paragraph()
            list_items.append(_render_inline(stripped[2:].strip()))
        else:
            flush_list()
            paragraph.append(stripped)
    flush_paragraph()
    flush_list()
    return Markup("\n".join(parts))


def format_price_rub(amount_kop: int, suffix: str = "") -> str:
    """Сумма из целых копеек; копейки показываем, если сумма не круглая."""
    amount = int(amount_kop or 0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    rub = amount // 100
    kop = amount % 100
    int_part = f"{rub:,}".replace(",", " ")
    if kop:
        return f"{sign}{int_part}.{kop:02d} ₽{suffix}"
    return f"{sign}{int_part} ₽{suffix}"


def tariff_amount_kop(tariff: Tariff, period: SubscriptionPeriod | str) -> int:
    from app.services.billing import (
        year_amount_from_month_kop,
        years2_amount_from_month_kop,
    )

    per = period if isinstance(period, SubscriptionPeriod) else SubscriptionPeriod(str(period))
    month = int(tariff.price_month_kop or 0)
    if per == SubscriptionPeriod.years_2:
        return years2_amount_from_month_kop(month)
    if per == SubscriptionPeriod.year:
        return year_amount_from_month_kop(month)
    return month


def tariff_price_label(tariff: Tariff, period: SubscriptionPeriod | str) -> str:
    per = period if isinstance(period, SubscriptionPeriod) else SubscriptionPeriod(str(period))
    if per == SubscriptionPeriod.years_2:
        suffix = "/2 года"
    elif per == SubscriptionPeriod.year:
        suffix = "/год"
    else:
        suffix = "/мес"
    return format_price_rub(tariff_amount_kop(tariff, per), suffix)


def tariff_blurb(tariff: Tariff) -> str:
    meta = DEFAULT_TARIFF_META.get(tariff.code, {})
    return (tariff.blurb or str(meta.get("blurb") or "")).strip()


def tariff_features(tariff: Tariff) -> list[str]:
    if isinstance(tariff.features, list) and tariff.features:
        return [str(item) for item in tariff.features if str(item).strip()]
    meta = DEFAULT_TARIFF_META.get(tariff.code, {})
    return [str(item) for item in (meta.get("features") or [])]


def list_public_tariffs(db: Session) -> list[Tariff]:
    ensure_tariffs(db)
    return list(
        db.scalars(
            select(Tariff)
            .where(Tariff.is_active.is_(True))
            .order_by(Tariff.price_month_kop, Tariff.id)
        ).all()
    )


def get_content_slots(db: Session) -> dict[str, Markup]:
    rows = db.scalars(
        select(ContentBlock).where(ContentBlock.status == "published")
    ).all()
    out: dict[str, Markup] = {}
    for row in rows:
        if row.key not in CONTENT_SLOT_KEYS or not row.body_md.strip():
            continue
        out[row.key] = safe_markdown(row.body_md, inline=row.key in INLINE_SLOTS)
    return out


def get_or_create_content_block(db: Session, key: str) -> ContentBlock:
    if key not in CONTENT_SLOT_KEYS:
        raise ValueError("Неизвестный слот")
    row = db.get(ContentBlock, key)
    if row is None:
        row = ContentBlock(key=key, title=key, body_md="", status="draft", version=1)
        db.add(row)
        db.flush()
    return row


def _keep_last_content_versions(db: Session, key: str, keep: int = 10) -> None:
    versions = db.scalars(
        select(ContentBlockVersion)
        .where(ContentBlockVersion.block_key == key)
        .order_by(ContentBlockVersion.version.desc(), ContentBlockVersion.id.desc())
    ).all()
    for old in versions[keep:]:
        db.delete(old)


def publish_content_block(
    db: Session,
    *,
    key: str,
    title: str,
    body_md: str,
    status: str,
    user_id: int | None,
) -> ContentBlock:
    row = get_or_create_content_block(db, key)
    row.title = (title or key).strip()[:255]
    row.body_md = (body_md or "").strip()
    row.status = "published" if status == "published" else "draft"
    row.version = int(row.version or 0) + 1
    row.updated_by = user_id
    row.updated_at = utcnow()
    if row.status == "published":
        db.add(
            ContentBlockVersion(
                block_key=row.key,
                title=row.title,
                body_md=row.body_md,
                status=row.status,
                version=row.version,
                created_by=user_id,
            )
        )
    _keep_last_content_versions(db, row.key)
    record_event(
        db,
        type="cms_content_publish",
        org_id=None,
        user_id=user_id,
        details={"key": row.key, "status": row.status, "version": row.version},
        commit=False,
    )
    if row.status == "published":
        purge_public_cache()
    db.flush()
    return row


def rollback_content_block(
    db: Session,
    *,
    key: str,
    version: int,
    user_id: int | None,
) -> ContentBlock:
    row = get_or_create_content_block(db, key)
    snap = db.scalar(
        select(ContentBlockVersion).where(
            ContentBlockVersion.block_key == key,
            ContentBlockVersion.version == version,
        )
    )
    if snap is None:
        raise ValueError("Версия не найдена")
    row.title = snap.title
    row.body_md = snap.body_md
    row.status = "published"
    row.version = int(row.version or 0) + 1
    row.updated_by = user_id
    row.updated_at = utcnow()
    db.add(
        ContentBlockVersion(
            block_key=row.key,
            title=row.title,
            body_md=row.body_md,
            status=row.status,
            version=row.version,
            created_by=user_id,
        )
    )
    _keep_last_content_versions(db, row.key)
    record_event(
        db,
        type="cms_content_rollback",
        org_id=None,
        user_id=user_id,
        details={"key": row.key, "from_version": version, "version": row.version},
        commit=False,
    )
    purge_public_cache()
    db.flush()
    return row


def _rub_to_kop(value: object) -> int:
    raw = str(value or "").strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    if not raw:
        return 0
    return max(0, int(round(float(raw) * 100)))


def _features_from_text(value: object) -> list[str]:
    text = str(value or "")
    return [line.strip(" -\t") for line in text.splitlines() if line.strip(" -\t")]


def update_tariff_prices(
    db: Session,
    data: Mapping[str, object],
    *,
    user_id: int | None,
) -> int:
    changed = 0
    from app.services.billing import year_amount_from_month_kop

    for tariff in db.scalars(select(Tariff).order_by(Tariff.id)).all():
        code = tariff.code.value
        new_month = _rub_to_kop(data.get(f"price_month_{code}"))
        new_year = year_amount_from_month_kop(new_month)
        new_blurb = str(data.get(f"blurb_{code}") or "").strip()
        new_features = _features_from_text(data.get(f"features_{code}"))
        old = (
            tariff.price_month_kop,
            tariff.price_year_kop,
            tariff.blurb or "",
            list(tariff.features or []),
        )
        if (
            old[0] == new_month
            and old[1] == new_year
            and old[2] == new_blurb
            and old[3] == new_features
        ):
            continue
        db.add(
            TariffPriceLog(
                tariff_id=tariff.id,
                tariff_code=code,
                old_price_month_kop=tariff.price_month_kop,
                new_price_month_kop=new_month,
                old_price_year_kop=tariff.price_year_kop,
                new_price_year_kop=new_year,
                old_blurb=tariff.blurb or "",
                new_blurb=new_blurb,
                old_features=list(tariff.features or []),
                new_features=new_features,
                updated_by=user_id,
            )
        )
        tariff.price_month_kop = new_month
        tariff.price_year_kop = new_year
        tariff.blurb = new_blurb
        tariff.features = new_features
        changed += 1
    if changed:
        record_event(
            db,
            type="cms_tariffs_changed",
            org_id=None,
            user_id=user_id,
            details={"count": changed},
            commit=False,
        )
        purge_public_cache()
    db.flush()
    return changed


@dataclass(frozen=True)
class PromoResult:
    ok: bool
    code: str
    message: str
    base_amount_kop: int
    discount_kop: int
    final_amount_kop: int
    promo: PromoCode | None = None


def normalize_promo_code(code: str | None) -> str:
    return (code or "").strip().upper()


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def validate_promo_code(
    db: Session,
    *,
    code: str | None,
    tariff: Tariff,
    period: SubscriptionPeriod,
    base_amount_kop: int,
    now: datetime | None = None,
) -> PromoResult:
    normalized = normalize_promo_code(code)
    if not normalized:
        return PromoResult(True, "", "", base_amount_kop, 0, base_amount_kop, None)
    promo = db.scalar(select(PromoCode).where(PromoCode.code == normalized))
    if promo is None or not promo.is_active:
        return PromoResult(False, normalized, "Промокод не найден или выключен", base_amount_kop, 0, base_amount_kop)
    now = now or utcnow()
    start = _as_aware(promo.valid_from)
    end = _as_aware(promo.valid_to)
    if start and now < start:
        return PromoResult(False, normalized, "Промокод ещё не действует", base_amount_kop, 0, base_amount_kop, promo)
    if end and now > end:
        return PromoResult(False, normalized, "Срок действия промокода истёк", base_amount_kop, 0, base_amount_kop, promo)
    reserved = count_promo_reserves(db, promo.id, now=now)
    if promo.max_uses is not None and int(promo.used_count or 0) + reserved >= promo.max_uses:
        return PromoResult(False, normalized, "Лимит использований промокода исчерпан", base_amount_kop, 0, base_amount_kop, promo)
    tariff_codes = [str(x) for x in (promo.tariff_codes or []) if str(x).strip()]
    if tariff_codes and tariff.code.value not in tariff_codes:
        return PromoResult(False, normalized, "Промокод не подходит для выбранного тарифа", base_amount_kop, 0, base_amount_kop, promo)
    periods = [str(x) for x in (promo.periods or []) if str(x).strip()]
    if periods and period.value not in periods:
        return PromoResult(False, normalized, "Промокод не подходит для выбранного периода", base_amount_kop, 0, base_amount_kop, promo)
    if base_amount_kop <= 0:
        return PromoResult(False, normalized, "Промокод нельзя применить к нулевой сумме", base_amount_kop, 0, base_amount_kop, promo)
    if promo.type == PromoCodeType.percent:
        discount = base_amount_kop * max(0, int(promo.value or 0)) // 100
    else:
        discount = max(0, int(promo.value or 0))
    discount = min(discount, base_amount_kop - 1)
    if discount <= 0:
        return PromoResult(False, normalized, "Промокод не даёт скидку", base_amount_kop, 0, base_amount_kop, promo)
    final = base_amount_kop - discount
    if final <= 0:
        return PromoResult(False, normalized, "Итоговая сумма должна быть больше нуля", base_amount_kop, 0, base_amount_kop, promo)
    return PromoResult(True, normalized, "Промокод применён", base_amount_kop, discount, final, promo)


# W-48: бронь промокода на Init; used_count растёт только на CONFIRMED.
PROMO_RESERVE_TTL = timedelta(hours=24)


def count_promo_reserves(
    db: Session,
    promo_id: int,
    *,
    now: datetime | None = None,
    exclude_payment_id: object | None = None,
) -> int:
    """Активные Init с этим промо (created/authorized), ещё не expired."""
    from app.models import Payment, PaymentStatus

    now = now or utcnow()
    cutoff = now - PROMO_RESERVE_TTL
    stmt = select(func.count()).select_from(Payment).where(
        Payment.promo_code_id == promo_id,
        Payment.status.in_([PaymentStatus.created, PaymentStatus.authorized]),
        Payment.created_at >= cutoff,
    )
    if exclude_payment_id is not None:
        stmt = stmt.where(Payment.id != exclude_payment_id)
    return int(db.scalar(stmt) or 0)


def apply_promo_to_payment(
    db: Session,
    payment: Payment,
    result: PromoResult,
) -> None:
    """Reserve: суммы + promo_code_id. used_count не трогаем (commit на CONFIRMED)."""
    payment.amount_base_kop = result.base_amount_kop
    payment.discount_kop = result.discount_kop
    payment.amount_kop = result.final_amount_kop
    if result.promo is not None and result.discount_kop > 0:
        payment.promo_code_id = result.promo.id


def commit_promo_for_payment(db: Session, payment: Payment) -> None:
    """Commit брони: +1 к used_count при первом CONFIRMED."""
    if not payment.promo_code_id:
        return
    promo = db.get(PromoCode, payment.promo_code_id)
    if promo is None:
        return
    promo.used_count = int(promo.used_count or 0) + 1


def recalculate_promo_used_counts(
    db: Session,
    *,
    codes: list[str] | None = None,
) -> dict[str, int]:
    """used_count = число confirmed-платежей с этим promo_code_id."""
    from app.models import Payment, PaymentStatus

    stmt = select(PromoCode)
    if codes:
        normalized = [normalize_promo_code(c) for c in codes if c]
        stmt = stmt.where(PromoCode.code.in_(normalized))
    updated: dict[str, int] = {}
    for promo in db.scalars(stmt).all():
        n = int(
            db.scalar(
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.promo_code_id == promo.id,
                    Payment.status == PaymentStatus.confirmed,
                )
            )
            or 0
        )
        promo.used_count = n
        updated[promo.code] = n
    return updated


def _csv_list(raw: object) -> list[str]:
    return [part.strip() for part in str(raw or "").replace("\n", ",").split(",") if part.strip()]


def _dt(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) == 10:
        text += "T00:00:00+00:00"
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def upsert_promo_code(db: Session, data: Mapping[str, object]) -> PromoCode:
    promo_id = int(str(data.get("id") or "0") or 0)
    row = db.get(PromoCode, promo_id) if promo_id else None
    if row is None:
        row = PromoCode(code=normalize_promo_code(str(data.get("code") or "")))
        db.add(row)
    row.code = normalize_promo_code(str(data.get("code") or row.code))
    row.type = PromoCodeType(str(data.get("type") or PromoCodeType.percent.value))
    row.value = max(0, int(str(data.get("value") or "0")))
    row.tariff_codes = _csv_list(data.get("tariff_codes"))
    row.periods = _csv_list(data.get("periods"))
    row.valid_from = _dt(data.get("valid_from"))
    row.valid_to = _dt(data.get("valid_to"))
    max_uses = str(data.get("max_uses") or "").strip()
    row.max_uses = int(max_uses) if max_uses else None
    row.is_active = bool(data.get("is_active"))
    row.updated_at = utcnow()
    db.flush()
    return row


def published_reviews(slots: Mapping[str, Markup]) -> list[dict[str, Markup | str]]:
    """Отзывы для лендинга: только полные слоты (имя + текст). Пустые — не выдумывать."""
    out: list[dict[str, Markup | str]] = []
    for idx in (1, 2, 3):
        name = slots.get(f"review_{idx}_name")
        text = slots.get(f"review_{idx}_text")
        if not name or not str(name).strip() or not text or not str(text).strip():
            continue
        role = slots.get(f"review_{idx}_role") or ""
        out.append({"name": name, "role": role, "text": text})
    return out


def launch_offer_public(db: Session) -> dict | None:
    """Акция запуска: остаток мест = max_uses − used_count у промокода (честный дефицит).

    Код по умолчанию BETA50; переопределение — опубликованный слот launch_promo_code.
    Без активного промокода с max_uses возвращает None (не рисуем «осталось N»).
    """
    code = DEFAULT_LAUNCH_PROMO_CODE
    slot = db.get(ContentBlock, "launch_promo_code")
    if (
        slot is not None
        and slot.status == "published"
        and (slot.body_md or "").strip()
    ):
        code = normalize_promo_code(slot.body_md.strip().splitlines()[0])
    if not code:
        return None
    promo = db.scalar(select(PromoCode).where(PromoCode.code == code))
    if promo is None or not promo.is_active:
        return None
    if promo.max_uses is None or int(promo.max_uses) <= 0:
        return None
    now = utcnow()
    start = _as_aware(promo.valid_from)
    end = _as_aware(promo.valid_to)
    if start and now < start:
        return None
    if end and now > end:
        return None
    max_uses = int(promo.max_uses)
    used = int(promo.used_count or 0)
    remaining = max(0, max_uses - used)
    percent = int(promo.value or 0) if promo.type == PromoCodeType.percent else None
    return {
        "code": promo.code,
        "max_uses": max_uses,
        "used_count": used,
        "remaining": remaining,
        "percent": percent,
        "exhausted": remaining <= 0,
    }


def active_announcement(db: Session, *, dismissed_id: str | None = None) -> dict | None:
    now = utcnow()
    rows = db.scalars(
        select(Announcement)
        .where(Announcement.is_active.is_(True), Announcement.status == "published")
        .order_by(Announcement.updated_at.desc(), Announcement.id.desc())
    ).all()
    for row in rows:
        if dismissed_id and dismissed_id == str(row.id):
            continue
        start = _as_aware(row.valid_from)
        end = _as_aware(row.valid_to)
        if start and now < start:
            continue
        if end and now > end:
            continue
        if not row.body_md.strip() and not row.title.strip():
            continue
        return {
            "id": row.id,
            "title": row.title,
            "html": safe_markdown(row.body_md, inline=True),
        }
    return None


def upsert_announcement(
    db: Session,
    data: Mapping[str, object],
    *,
    user_id: int | None,
) -> Announcement:
    row_id = int(str(data.get("id") or "0") or 0)
    row = db.get(Announcement, row_id) if row_id else None
    if row is None:
        row = Announcement()
        db.add(row)
    row.title = str(data.get("title") or "").strip()[:255]
    row.body_md = str(data.get("body_md") or "").strip()
    row.status = "published" if data.get("status") == "published" else "draft"
    row.valid_from = _dt(data.get("valid_from"))
    row.valid_to = _dt(data.get("valid_to"))
    row.is_active = bool(data.get("is_active"))
    row.updated_by = user_id
    row.updated_at = utcnow()
    record_event(
        db,
        type="cms_announcement_publish",
        org_id=None,
        user_id=user_id,
        details={"status": row.status, "active": row.is_active},
        commit=False,
    )
    if row.status == "published":
        purge_public_cache()
    db.flush()
    return row
