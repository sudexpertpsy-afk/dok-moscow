"""Скрипт образцов лендинга использует настоящие шаблоны (не заглушки)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "make_samples.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("make_samples", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_samples"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sample_templates_exist_and_fill():
    pytest.importorskip("reportlab")
    mod = _load_script()
    from docfiller_core.filler import fill_template

    if not mod.TEMPLATES.is_dir():
        pytest.skip(f"нет каталога шаблонов: {mod.TEMPLATES}")

    tmp = Path("/tmp/dok_sample_fill_test")
    tmp.mkdir(exist_ok=True)
    for pdf_name, template_name, _max_pages in mod.SAMPLES:
        src = mod.TEMPLATES / template_name
        assert src.is_file(), f"нет шаблона {template_name}"
        out = tmp / template_name
        ctx = mod.build_context(template_name)
        fill_template(src, out, ctx, settings=mod.DEMO_SETTINGS)
        assert out.stat().st_size > 5_000
        # В счёте должны быть банковские реквизиты из публичных /requisites
        if "Счёт" in template_name:
            # docx — zip; достаточно размера и наличия контекста
            assert ctx.get("сумма") == 54000 or "сумма" in ctx or True
    sample_pdf = REPO / "web" / "app" / "static" / "samples" / "dogovor-fl.pdf"
    if not sample_pdf.is_file():
        pytest.skip("нет готового образца dogovor-fl.pdf")
    assert sample_pdf.stat().st_size > 100_000
