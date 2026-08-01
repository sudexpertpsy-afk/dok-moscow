"""Общие шаблоны Jinja2."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.config import get_settings
from app.hosting import public_url

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _tojson(value) -> Markup:
    return Markup(json.dumps(value, ensure_ascii=False).replace("<", "\\u003c"))


def _inject_globals() -> None:
    s = get_settings()
    templates.env.globals["public_base_url"] = s.public_base_url.rstrip("/")
    templates.env.globals["app_base_url"] = s.app_base_url.rstrip("/")
    templates.env.globals["public_url"] = public_url


templates.env.filters["tojson"] = _tojson
_inject_globals()
