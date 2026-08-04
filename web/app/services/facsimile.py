"""W-43: правила уместности факсимиле и контекст генерации."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.branding import (
    FACSIMILE_PLACEHOLDERS,
    PLACEHOLDER_TO_SLOT,
    SLOTS,
    list_slots_status,
    slot_exists,
    slot_path,
)


class FacsimilePolicy(str, Enum):
    allowed = "allowed"  # чекбокс по умолчанию вкл
    warn = "warn"  # чекбокс есть + предупреждение
    forbidden = "forbidden"  # чекбокс скрыт, плейсхолдеры не применяются


WARN_TEXT = (
    "Факсимиле на договоре действительно при соглашении сторон; "
    "акт с факсимиле может не принять бухгалтерия контрагента."
)

# Текст пункта договора (без {% if %}: W-41 запрещает statements в DOCX).
# В шаблоне — обычный плейсхолдер {{ признают_факсимиле }}; пусто, если чекбокс выкл.
FACSIMILE_CLAUSE_TEXT = (
    "Стороны признают юридическую силу документов, подписанных с использованием "
    "факсимиле печати и (или) подписи уполномоченных лиц, воспроизведённых "
    "механическим или электронным способом, если иное не следует из соглашения сторон "
    "или требований законодательства."
)

# Имена файлов шаблонов (без пути)
_ALLOWED: frozenset[str] = frozenset(
    {
        "Счёт_на_оплату.docx",
        "Счёт_на_оплату_юрлицо.docx",
        "Сопроводительное_письмо.docx",
        "СППЭ_информация_суду.docx",
        "Психология_ДРО_с_итогом.docx",
    }
)

_WARN: frozenset[str] = frozenset(
    {
        "Договор_услуги_v2.docx",
        "Договор_услуги_юрлицо.docx",
        "Договор_услуги_с_печатью.docx",
        "Договор_рецензия.docx",
        "Договор_рецензия_юрлицо.docx",
        "Договор_обучение_СПЭ.docx",
        "Договор_обучение_СПЭ_юрлицо.docx",
        "Договор_обучение_полиграф.docx",
        "Договор_освидетельствование.docx",
        "Договор_ГПД_эксперт.docx",
        "Акт_оказанных_услуг.docx",
        "Акт_оказанных_услуг_юрлицо.docx",
        "Акт_ГПД_эксперт.docx",
        "Допсоглашение_продление.docx",
        "Допсоглашение_продление_юрлицо.docx",
        "Соглашение_расторжение.docx",
        "Соглашение_расторжение_юрлицо.docx",
        "Уведомление_расторжение.docx",
        "Уведомление_расторжение_юрлицо.docx",
    }
)

_FORBIDDEN: frozenset[str] = frozenset(
    {
        "ПКО_КО-1.docx",
        "Заключение_эксперта_гражданский_процесс.docx",
        "Заключение_эксперта_уголовный_процесс.docx",
        "Согласие_ПДн.docx",
        "Ходатайство_о_назначении_экспертизы.docx",
    }
)


def facsimile_feature_enabled() -> bool:
    """Глобальный feature-flag FAKSIMILE_ENABLED (выключение при инциденте)."""
    return bool(get_settings().faksimile_enabled)


def normalize_template_name(name: str) -> str:
    return Path(name).name


def facsimile_policy(template_name: str) -> FacsimilePolicy:
    name = normalize_template_name(template_name)
    if name in _FORBIDDEN:
        return FacsimilePolicy.forbidden
    if name in _WARN:
        return FacsimilePolicy.warn
    if name in _ALLOWED:
        return FacsimilePolicy.allowed
    # пользовательские / неизвестные — разрешено с предупреждением осторожности
    stem = name.lower()
    if any(x in stem for x in ("пко", "заключен", "согласи", "ходатайств")):
        return FacsimilePolicy.forbidden
    if any(x in stem for x in ("договор", "акт", "допсогл", "расторж")):
        return FacsimilePolicy.warn
    if any(x in stem for x in ("счёт", "счет", "письм", "справк")):
        return FacsimilePolicy.allowed
    return FacsimilePolicy.allowed


def facsimile_default_on(template_name: str) -> bool:
    return facsimile_policy(template_name) == FacsimilePolicy.allowed


POLICY_LABELS: dict[FacsimilePolicy, str] = {
    FacsimilePolicy.allowed: "факсимиле разрешено",
    FacsimilePolicy.warn: "факсимиле с предупреждением",
    FacsimilePolicy.forbidden: "факсимиле запрещено",
}


def facsimile_ui(template_name: str) -> dict[str, Any]:
    if not facsimile_feature_enabled():
        return {
            "policy": FacsimilePolicy.forbidden.value,
            "show_checkbox": False,
            "default_on": False,
            "warn_text": None,
            "forbidden": True,
            "policy_label": "факсимиле выключено",
            "feature_off": True,
        }
    policy = facsimile_policy(template_name)
    return {
        "policy": policy.value,
        "show_checkbox": policy != FacsimilePolicy.forbidden,
        "default_on": policy == FacsimilePolicy.allowed,
        "warn_text": WARN_TEXT if policy == FacsimilePolicy.warn else None,
        "forbidden": policy == FacsimilePolicy.forbidden,
        "policy_label": POLICY_LABELS[policy],
        "feature_off": False,
    }


def attach_facsimile_policy(items: list[dict]) -> list[dict]:
    """Добавить метки уместности факсимиле в список шаблонов реестра."""
    out: list[dict] = []
    for it in items:
        row = dict(it)
        ui = facsimile_ui(row.get("name") or "")
        row["facsimile_policy"] = ui["policy"]
        row["facsimile_label"] = ui["policy_label"]
        row["facsimile_warn"] = ui["warn_text"]
        out.append(row)
    return out


def package_facsimile_summary(
    template_names: list[str],
    *,
    display_name=None,
) -> dict[str, Any]:
    """Сводка Д-1 для мастера комплекта."""
    dn = display_name or (lambda n: Path(n).stem.replace("_", " "))
    will_add: list[str] = []
    with_warn: list[str] = []
    skipped: list[str] = []
    rows: list[dict[str, Any]] = []
    feature_on = facsimile_feature_enabled()
    for name in template_names:
        ui = facsimile_ui(name)
        title = dn(name)
        policy = ui["policy"]
        row = {
            "name": name,
            "title": title,
            "show": ui["show_checkbox"],
            "checked": ui["default_on"] if feature_on else False,
            "warn": ui["warn_text"],
            "policy": policy,
        }
        rows.append(row)
        if not feature_on or policy == FacsimilePolicy.forbidden.value:
            skipped.append(title)
        elif policy == FacsimilePolicy.warn.value:
            with_warn.append(title)
        else:
            will_add.append(title)
    parts: list[str] = []
    if will_add:
        parts.append("Будет добавлено: " + ", ".join(will_add))
    if with_warn:
        parts.append("С предупреждением: " + ", ".join(with_warn) + " (подтвердите)")
    if skipped:
        parts.append("Не добавляется: " + ", ".join(skipped))
    return {
        "rows": rows,
        "text": " · ".join(parts) if parts else "",
        "will_add": will_add,
        "with_warn": with_warn,
        "skipped": skipped,
        "any_toggle": any(r["show"] for r in rows),
        "master_default": bool(will_add) and feature_on,
    }


def doc_has_facsimile(doc_or_context) -> bool:
    ctx = doc_or_context if isinstance(doc_or_context, dict) else (getattr(doc_or_context, "context", None) or {})
    return bool(ctx.get("_facsimile_pdf"))


def get_facsimile_prefs(requisites: dict | None) -> dict[str, Any]:
    raw = (requisites or {}).get("факсимиле") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "pdf_default": bool(raw.get("pdf_default", True)),
        "embed_docx": bool(raw.get("embed_docx", False)),
        "onboarding_dismissed": bool(raw.get("onboarding_dismissed", False)),
        "pdf_seen_without_branding": bool(raw.get("pdf_seen_without_branding", False)),
    }


def set_facsimile_prefs(
    requisites: dict,
    *,
    pdf_default: bool | None = None,
    embed_docx: bool | None = None,
    onboarding_dismissed: bool | None = None,
    pdf_seen_without_branding: bool | None = None,
) -> dict:
    block = dict(requisites.get("факсимиле") or {})
    if pdf_default is not None:
        block["pdf_default"] = bool(pdf_default)
    if embed_docx is not None:
        block["embed_docx"] = bool(embed_docx)
    if onboarding_dismissed is not None:
        block["onboarding_dismissed"] = bool(onboarding_dismissed)
    if pdf_seen_without_branding is not None:
        block["pdf_seen_without_branding"] = bool(pdf_seen_without_branding)
    requisites["факсимиле"] = block
    return requisites


def org_has_any_branding(org_id: int) -> bool:
    return any(slot_exists(org_id, s) for s in SLOTS)


def branding_slots_summary(org_id: int) -> list[dict[str, Any]]:
    """Для админки: какие слоты настроены (без показа изображений)."""
    return [
        {"slot": s["slot"], "label": s["label"], "exists": s["exists"]}
        for s in list_slots_status(org_id)
    ]


def count_orgs_with_branding(org_ids: list[int]) -> int:
    return sum(1 for oid in org_ids if org_has_any_branding(oid))


def images_for_fill(org_id: int) -> dict[str, Path]:
    """Плейсхолдер → путь PNG (только существующие слоты)."""
    if not facsimile_feature_enabled():
        return {}
    out: dict[str, Path] = {}
    for ph, slot in PLACEHOLDER_TO_SLOT.items():
        if slot_exists(org_id, slot):
            out[ph] = slot_path(org_id, slot)
    return out


def strip_facsimile_from_variables(variables: list[str]) -> list[str]:
    """Не показывать факсимиле_* в форме документа как текстовые поля."""
    return [v for v in variables if v not in FACSIMILE_PLACEHOLDERS]


def apply_facsimile_context_flags(
    context: dict,
    *,
    template_name: str,
    want_pdf: bool,
    want_docx_embed: bool,
) -> dict:
    """Сохранить флаги в context документа (не шаблонные поля)."""
    ctx = dict(context)
    if not facsimile_feature_enabled():
        ctx["_facsimile_pdf"] = False
        ctx["_facsimile_docx"] = False
        return ctx
    policy = facsimile_policy(template_name)
    if policy == FacsimilePolicy.forbidden:
        ctx["_facsimile_pdf"] = False
        ctx["_facsimile_docx"] = False
        return ctx
    ctx["_facsimile_pdf"] = bool(want_pdf)
    ctx["_facsimile_docx"] = bool(want_docx_embed) and bool(want_pdf)
    return ctx


def should_use_images_for_docx(context: dict | None) -> bool:
    if not facsimile_feature_enabled():
        return False
    return bool((context or {}).get("_facsimile_docx"))


def should_use_images_for_pdf(context: dict | None) -> bool:
    if not facsimile_feature_enabled():
        return False
    return bool((context or {}).get("_facsimile_pdf"))


def onboarding_tip_visible(requisites: dict | None, org_id: int) -> bool:
    """Плашка Д-4: после PDF без branding, пока не закрыли и branding пуст."""
    if not facsimile_feature_enabled():
        return False
    prefs = get_facsimile_prefs(requisites)
    if prefs.get("onboarding_dismissed"):
        return False
    if org_has_any_branding(org_id):
        return False
    return bool(prefs.get("pdf_seen_without_branding"))
