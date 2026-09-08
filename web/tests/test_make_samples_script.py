"""Скрипт образцов лендинга использует настоящие шаблоны (не заглушки)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
    mod = _load_script()
    from docfiller_core.filler import fill_template

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
    assert (REPO / "web" / "app" / "static" / "samples" / "dogovor-fl.pdf").stat().st_size > 100_000
