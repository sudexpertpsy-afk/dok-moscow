"""Манифесты шаблонов (*.manifest.yaml рядом с DOCX).

Формат (кириллические ключи, как в Шаблонере):

    описание: …
    группа: Заключения эксперта
    поля:
      номер_заключения: {тип: счётчик, подпись: …, обязательное: да}
      вопросы_эксперту: {тип: многострочный, …}
      дата_заключения: {тип: дата, по_умолчанию: сегодня}
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from .utils import normalize_name

# Порядок групп в каталоге кабинета (после «Письма суду» — заключения).
GROUP_ORDER = (
    "Договоры",
    "Счета и акты",
    "Кассовые",
    "Банковские",
    "Письма суду",
    "Заключения эксперта",
    "Прочее",
)

_TYPE_MAP = {
    "строка": "string",
    "string": "string",
    "многострочный": "multiline",
    "multiline": "multiline",
    "дата": "date",
    "date": "date",
    "счётчик": "counter",
    "счетчик": "counter",
    "counter": "counter",
    "деньги": "money",
    "money": "money",
    "флажок": "checkbox",
    "checkbox": "checkbox",
    "выбор": "select",
    "select": "select",
}

_lock = threading.RLock()
_cache: dict[str, "TemplateManifest | None"] = {}


@dataclass
class FieldSpec:
    name: str
    type: str = "string"
    label: str = ""
    required: bool = False
    hint: str = ""
    default: str = ""
    options: list[str] = field(default_factory=list)

    def as_registry_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label or self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "hint": self.hint,
            "options": list(self.options),
        }


@dataclass
class TemplateManifest:
    stem: str
    description: str = ""
    group: str = ""
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    # Тип документа для журнала/реестра: заключение | договор | документ
    kind: str = "документ"
    # Ключ счётчика для полей типа counter (если один на шаблон).
    counter_key: str = ""
    counter_suffix_year: bool = False  # формат N/ГГ

    @property
    def docx_name(self) -> str:
        return f"{self.stem}.docx"


def manifest_path_for(docx_path: Path) -> Path:
    stem = normalize_name(Path(docx_path).stem)
    return Path(docx_path).with_name(f"{stem}.manifest.yaml")


def _parse_field(name: str, raw: Any) -> FieldSpec:
    if not isinstance(raw, dict):
        return FieldSpec(name=name, label=name)
    ftype = _TYPE_MAP.get(str(raw.get("тип") or raw.get("type") or "строка").strip().lower(), "string")
    options = raw.get("options") or raw.get("варианты") or []
    if not isinstance(options, list):
        options = []
    default = raw.get("по_умолчанию", raw.get("default", ""))
    if default is None:
        default = ""
    return FieldSpec(
        name=name,
        type=ftype,
        label=str(raw.get("подпись") or raw.get("label") or name).strip() or name,
        required=bool(raw.get("обязательное", raw.get("required", False))),
        hint=str(raw.get("подсказка") or raw.get("hint") or "").strip(),
        default=str(default).strip(),
        options=[str(x).strip() for x in options if str(x).strip()],
    )


def _infer_kind(stem: str, raw: dict) -> str:
    explicit = str(raw.get("тип_документа") or raw.get("kind") or "").strip().lower()
    if explicit in {"заключение", "договор", "документ"}:
        return explicit
    s = stem.casefold()
    if s.startswith("заключение"):
        return "заключение"
    if s.startswith("договор"):
        return "договор"
    return "документ"


def _default_counter_key(stem: str, kind: str, fields: dict[str, FieldSpec] | None = None) -> str:
    fields = fields or {}
    if "исх_номер" in fields and fields["исх_номер"].type == "counter":
        return "ishod"
    if kind != "заключение":
        return ""
    s = stem.casefold().replace("ё", "е")
    if "ugolov" in s or "уголовн" in s:
        return "zaklyuchenie_upk"
    if "grazhd" in s or "гражданск" in s:
        return "zaklyuchenie_gpk"
    return f"zaklyuchenie_{stem[:40]}"


def parse_manifest_dict(stem: str, raw: dict | None) -> TemplateManifest:
    data = raw if isinstance(raw, dict) else {}
    fields_raw = data.get("поля") or data.get("fields") or {}
    fields: dict[str, FieldSpec] = {}
    if isinstance(fields_raw, dict):
        for key, val in fields_raw.items():
            name = str(key).strip()
            if name:
                fields[name] = _parse_field(name, val)
    kind = _infer_kind(stem, data)
    counter_key = str(data.get("счётчик") or data.get("counter_key") or "").strip()
    if not counter_key:
        counter_key = _default_counter_key(stem, kind, fields)
    has_counter = any(f.type == "counter" for f in fields.values())
    return TemplateManifest(
        stem=stem,
        description=str(data.get("описание") or data.get("description") or "").strip(),
        group=str(data.get("группа") or data.get("group") or "").strip(),
        fields=fields,
        kind=kind,
        counter_key=counter_key,
        counter_suffix_year=bool(
            data.get("счётчик_год", data.get("counter_year", has_counter and kind == "заключение"))
        ),
    )


def load_manifest(docx_or_manifest: Path | str, *, force: bool = False) -> TemplateManifest | None:
    """Загрузить манифест для DOCX или по пути *.manifest.yaml."""
    path = Path(docx_or_manifest)
    if path.suffix.lower() == ".yaml" or path.name.endswith(".manifest.yaml"):
        man_path = path
        stem = normalize_name(path.name.replace(".manifest.yaml", "").removesuffix(".yaml"))
    else:
        man_path = manifest_path_for(path)
        stem = normalize_name(path.stem)
    cache_key = str(man_path.resolve()) if man_path.exists() else f"missing:{stem}"
    with _lock:
        if not force and cache_key in _cache:
            return _cache[cache_key]
    if yaml is None or not man_path.is_file():
        with _lock:
            _cache[cache_key] = None
        return None
    try:
        raw = yaml.safe_load(man_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        with _lock:
            _cache[cache_key] = None
        return None
    man = parse_manifest_dict(stem, raw if isinstance(raw, dict) else {})
    with _lock:
        _cache[cache_key] = man
    return man


def invalidate_manifest_cache() -> None:
    with _lock:
        _cache.clear()


def infer_group(template_name: str, manifest: TemplateManifest | None = None) -> str:
    if manifest and manifest.group:
        return manifest.group
    n = unicodedata.normalize("NFC", template_name).casefold().replace("ё", "е")
    stem = Path(template_name).stem.casefold().replace("ё", "е")
    if stem.startswith("договор"):
        return "Договоры"
    if "счет_на_оплату" in n or "счёт_на_оплату" in template_name.casefold() or stem.startswith("акт_"):
        return "Счета и акты"
    if stem.startswith("пко"):
        return "Кассовые"
    if (
        "суду" in n
        or stem.startswith("ходатайство")
        or "уведомление" in n
        or stem.startswith("сопроводительное")
    ):
        return "Письма суду"
    if stem.startswith("заключение"):
        return "Заключения эксперта"
    return "Прочее"


def group_sort_key(group: str) -> tuple[int, str]:
    try:
        idx = GROUP_ORDER.index(group)
    except ValueError:
        idx = len(GROUP_ORDER)
    return (idx, group.casefold())


def document_kind(template_name: str, *, templates_dir: Path | None = None) -> str:
    """заключение | договор | документ — для журнала и реестра."""
    name = Path(template_name).name
    stem = Path(name).stem
    if templates_dir is not None:
        man = load_manifest(Path(templates_dir) / name)
        if man is not None:
            return man.kind
    s = stem.casefold()
    if s.startswith("заключение"):
        return "заключение"
    if s.startswith("договор"):
        return "договор"
    return "документ"


def counter_key_for(template_name: str, field_name: str, *, templates_dir: Path | None = None) -> str | None:
    """Ключ счётчика: у заключений — свой на шаблон; исх_номер — общий ishod."""
    man = None
    if templates_dir is not None:
        man = load_manifest(Path(templates_dir) / Path(template_name).name)
    if man and field_name in man.fields and man.fields[field_name].type == "counter":
        if man.counter_key:
            return man.counter_key
    if field_name == "номер_заключения":
        return _default_counter_key(Path(template_name).stem, "заключение")
    if field_name == "исх_номер":
        return "ishod"
    return None
