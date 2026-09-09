"""W-45/Б-2: byte-identical эталоны встроенных шаблонов без факсимиле."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.services.templates import ensure_core_on_path, resolve_template_path

BASELINES = Path(__file__).resolve().parent / "baselines" / "facsimile_clean.json"
_REPO = Path(__file__).resolve().parents[2]
TEMPLATES_ROOT = _REPO / "core" / "Шаблоны"
if not TEMPLATES_ROOT.is_dir():
    TEMPLATES_ROOT = Path("/workspace/core/Шаблоны")


def _doc_xml_sha256(docx_path: Path) -> str:
    with ZipFile(docx_path) as zf:
        data = zf.read("word/document.xml")
    return hashlib.sha256(data).hexdigest()


def _builtin_names() -> list[str]:
    if not TEMPLATES_ROOT.is_dir():
        return []
    return sorted(p.name for p in TEMPLATES_ROOT.glob("*.docx") if not p.name.startswith("~$"))


@pytest.mark.skipif(not TEMPLATES_ROOT.is_dir(), reason="нет core/Шаблоны")
def test_facsimile_clean_baselines_match():
    """Заполнение без images не меняет document.xml относительно эталона W-43."""
    ensure_core_on_path()
    from docfiller_core.filler import fill_template

    assert BASELINES.is_file(), f"нет эталона {BASELINES}"
    expected: dict[str, str] = json.loads(BASELINES.read_text(encoding="utf-8"))
    names = _builtin_names()
    assert names, "пустой каталог шаблонов"
    # эталон покрывает все текущие встроенные
    assert set(expected) == set(names), (
        f"расхождение имён шаблонов: +{set(names)-set(expected)} "
        f"-{set(expected)-set(names)}"
    )

    import tempfile

    ctx = {
        "фио_клиента": "Тест",
        "сумма": "1000",
        "наименование_услуги": "Услуга",
        "номер_договора": "1",
        "дата_договора": "01.01.2026",
        "номер_счёта": "1",
        "дата_счёта": "01.01.2026",
        "номер_акта": "1",
        "дата_акта": "01.01.2026",
    }
    mismatches: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name in names:
            src = resolve_template_path(name, org_id=None)
            out = tmp_path / name
            # пустой/минимальный контекст — filler подставляет что есть
            fill_template(src, out, ctx, settings={}, images=None)
            digest = _doc_xml_sha256(out)
            if digest != expected[name]:
                mismatches.append(f"{name}: got={digest} want={expected[name]}")
            # без media при выключенном факсимиле
            with ZipFile(out) as zf:
                media = [n for n in zf.namelist() if n.startswith("word/media/")]
            # исходный шаблон мог уже содержать картинки логотипа — сравниваем с исходником
            with ZipFile(src) as zf:
                src_media = [n for n in zf.namelist() if n.startswith("word/media/")]
            assert len(media) == len(src_media), f"{name}: добавились media без факсимиле"
    assert not mismatches, "эталоны document.xml изменились:\n" + "\n".join(mismatches)


def _generate_baselines() -> None:
    """Утилита: пересобрать эталоны (запуск вручную при осознанном обновлении)."""
    ensure_core_on_path()
    from docfiller_core.filler import fill_template

    import tempfile

    ctx = {
        "фио_клиента": "Тест",
        "сумма": "1000",
        "наименование_услуги": "Услуга",
        "номер_договора": "1",
        "дата_договора": "01.01.2026",
        "номер_счёта": "1",
        "дата_счёта": "01.01.2026",
        "номер_акта": "1",
        "дата_акта": "01.01.2026",
    }
    out_map: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name in _builtin_names():
            src = resolve_template_path(name, org_id=None)
            out = tmp_path / name
            fill_template(src, out, ctx, settings={}, images=None)
            out_map[name] = _doc_xml_sha256(out)
    BASELINES.parent.mkdir(parents=True, exist_ok=True)
    BASELINES.write_text(
        json.dumps(out_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {BASELINES} ({len(out_map)} templates)")


if __name__ == "__main__":
    _generate_baselines()
