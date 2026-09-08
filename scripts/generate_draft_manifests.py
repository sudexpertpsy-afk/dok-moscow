#!/usr/bin/env python3
"""Черновики *.manifest.yaml для DOCX без манифеста (W-46 §6).

Запуск из корня репо или core/:
  python scripts/generate_draft_manifests.py
  python scripts/generate_draft_manifests.py --force   # перезаписать все черновики

Существующие манифесты без маркера «черновик W-46» не трогаем (если не --force).
Подписи вычитывает владелец; генератор опирается на labels.py и эвристики типов.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
sys.path.insert(0, str(CORE))

from docfiller_core.filler import describe_template, list_template_variables  # noqa: E402
from docfiller_core.labels import подпись  # noqa: E402
from docfiller_core.template_manifest import infer_group, manifest_path_for  # noqa: E402
from docfiller_core.utils import is_date_field, is_multiline_field  # noqa: E402

DRAFT_MARK = "# черновик W-46: подписи вычитать владельцу"
TEMPLATES = CORE / "Шаблоны"

_COUNTER_NAMES = {
    "номер_договора",
    "номер_акта",
    "номер_счёта",
    "номер_пко",
    "номер_рко",
    "номер_платёжки",
    "номер_упд",
    "номер_сф",
    "номер_сверки",
    "номер_допсоглашения",
    "номер_заключения",
    "исх_номер",
}

_MONEY_PREFIXES = ("сумма", "цена", "стоимость", "оплата", "ндс")
_REQUIRED = {
    "номер_договора",
    "номер_акта",
    "номер_счёта",
    "номер_пко",
    "исх_номер",
    "номер_заключения",
    "фио_клиента",
    "название_заказчика",
    "фио_эксперта",
}


def _field_type(name: str) -> str:
    if name in _COUNTER_NAMES or (name.startswith("номер_") and name not in {"номер_дела", "номер_доверенности", "номер_рецензируемого"}):
        return "счётчик"
    if is_date_field(name):
        return "дата"
    if is_multiline_field(name):
        return "многострочный"
    low = name.casefold()
    if any(low.startswith(p) or p in low for p in _MONEY_PREFIXES):
        if "ставка" in low:
            return "строка"
        return "деньги"
    return "строка"


def _yaml_escape(text: str) -> str:
    if any(c in text for c in ":#{}[]&*!|>'\"%@`") or text.strip() != text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def build_manifest_text(docx: Path) -> str:
    stem = docx.stem
    vars_ = list_template_variables(docx)
    desc = describe_template(docx) or stem.replace("_", " ")
    group = infer_group(docx.name)
    lines = [
        DRAFT_MARK,
        f"описание: {_yaml_escape(desc)}",
        f"группа: {group}",
        "поля:",
    ]
    for name in vars_:
        ftype = _field_type(name)
        label = подпись(name)
        parts = [f"тип: {ftype}", f"подпись: {_yaml_escape(label)}"]
        if ftype == "дата":
            parts.append("по_умолчанию: сегодня")
        if name in _REQUIRED:
            parts.append("обязательное: да")
        lines.append(f"  {name}: {{{', '.join(parts)}}}")
    if not vars_:
        lines.append("  # (переменных в DOCX не найдено)")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="перезаписать и curated-манифесты")
    ap.add_argument("--dir", type=Path, default=TEMPLATES)
    args = ap.parse_args()
    root: Path = args.dir
    if not root.is_dir():
        print(f"✗ нет каталога {root}", file=sys.stderr)
        return 1

    written = 0
    skipped = 0
    for docx in sorted(root.glob("*.docx")):
        if docx.name.startswith("~$"):
            continue
        man = manifest_path_for(docx)
        if man.is_file() and not args.force:
            text = man.read_text(encoding="utf-8")
            if DRAFT_MARK not in text:
                skipped += 1
                continue
            # уже черновик — обновим
        body = build_manifest_text(docx)
        man.write_text(body, encoding="utf-8")
        written += 1
        print(f"→ {man.name}")
    print(f"✓ записано {written}, пропущено curated {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
