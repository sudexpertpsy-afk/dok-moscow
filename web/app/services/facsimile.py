"""W-43: правила уместности факсимиле и контекст генерации."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from app.services.branding import (
    FACSIMILE_PLACEHOLDERS,
    PLACEHOLDER_TO_SLOT,
    SLOTS,
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
    policy = facsimile_policy(template_name)
    return {
        "policy": policy.value,
        "show_checkbox": policy != FacsimilePolicy.forbidden,
        "default_on": policy == FacsimilePolicy.allowed,
        "warn_text": WARN_TEXT if policy == FacsimilePolicy.warn else None,
        "forbidden": policy == FacsimilePolicy.forbidden,
        "policy_label": POLICY_LABELS[policy],
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


def get_facsimile_prefs(requisites: dict | None) -> dict[str, Any]:
    raw = (requisites or {}).get("факсимиле") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "pdf_default": bool(raw.get("pdf_default", True)),
        "embed_docx": bool(raw.get("embed_docx", False)),
    }


def set_facsimile_prefs(requisites: dict, *, pdf_default: bool, embed_docx: bool) -> dict:
    block = dict(requisites.get("факсимиле") or {})
    block["pdf_default"] = bool(pdf_default)
    block["embed_docx"] = bool(embed_docx)
    requisites["факсимиле"] = block
    return requisites


def org_has_any_branding(org_id: int) -> bool:
    return any(slot_exists(org_id, s) for s in SLOTS)


def images_for_fill(org_id: int) -> dict[str, Path]:
    """Плейсхолдер → путь PNG (только существующие слоты)."""
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
    policy = facsimile_policy(template_name)
    ctx = dict(context)
    if policy == FacsimilePolicy.forbidden:
        ctx["_facsimile_pdf"] = False
        ctx["_facsimile_docx"] = False
        return ctx
    ctx["_facsimile_pdf"] = bool(want_pdf)
    ctx["_facsimile_docx"] = bool(want_docx_embed) and bool(want_pdf)
    return ctx


def should_use_images_for_docx(context: dict | None) -> bool:
    return bool((context or {}).get("_facsimile_docx"))


def should_use_images_for_pdf(context: dict | None) -> bool:
    return bool((context or {}).get("_facsimile_pdf"))
