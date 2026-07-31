"""Общие шаблоны Jinja2."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _tojson(value) -> Markup:
    return Markup(json.dumps(value, ensure_ascii=False).replace("<", "\\u003c"))


templates.env.filters["tojson"] = _tojson
