"""Хэшированная статика и static_url (W-31)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, str]:
    path = _STATIC_DIR / "manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def static_url(path: str) -> str:
    """Вернуть /static/... с хэшем из manifest, иначе исходный путь."""
    name = path.strip()
    if name.startswith("/static/"):
        name = name[len("/static/") :]
    name = name.lstrip("/")
    hashed = load_manifest().get(name, name)
    return f"/static/{hashed}"


def clear_manifest_cache() -> None:
    load_manifest.cache_clear()
