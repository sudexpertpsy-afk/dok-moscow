"""Публичное демо ЕГРЮЛ для лендинга + контент-хуки."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document
from app.services.dadata import PartyCard, _cache, _headers, _request, parse_party_suggestion

log = logging.getLogger("dok.landing_demo")

_DATA = Path(__file__).resolve().parents[1] / "data"


@lru_cache
def load_demo_examples() -> list[dict[str, Any]]:
    path = _DATA / "demo_inn.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("demo_inn.json")
        return []


@lru_cache
def load_content_hooks() -> list[dict[str, str]]:
    path = _DATA / "landing_content_hooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("landing_content_hooks.json")
        return []


def find_party_public(inn: str) -> PartyCard | None:
    """findById без привязки к org (кэш общий с кабинетом)."""
    q = "".join(ch for ch in (inn or "") if ch.isdigit())
    if len(q) not in (10, 12):
        return None
    cache_key = f"find:{q}"
    hit, cached = _cache.get_party(cache_key)
    if hit:
        return cached
    if _headers() is None:
        return None
    raw_list = _request("findById/party", {"query": q})
    if not raw_list:
        _cache.set_party(cache_key, None)
        return None
    card = parse_party_suggestion(raw_list[0])
    _cache.set_party(cache_key, card)
    return card


def party_to_demo_json(card: PartyCard) -> dict[str, Any]:
    mgr = " ".join(x for x in (card.management_post, card.management_name) if x).strip()
    if card.party_type == "INDIVIDUAL" and not mgr:
        mgr = card.fio
    return {
        "inn": card.inn,
        "name_short": card.name_short or card.name_full or card.fio,
        "name_full": card.name_full or card.name_short or card.fio,
        "ogrn": card.ogrnip or card.ogrn,
        "address": card.address,
        "management": mgr,
        "status_label": card.status_label or card.status,
        "status": card.status,
        "status_tone": card.status_tone,
    }


def beta_promo_copy(db: Session | None) -> dict[str, Any]:
    """Заголовок финальной формы заявки (без бета-акции 50%)."""
    _ = db  # совместимость вызова
    return {
        "title": "Оставить заявку",
        "used": 0,
        "remaining": 0,
        "total": 0,
        "show_remaining": False,
    }


def landing_stats(db: Session | None) -> dict[str, Any]:
    settings = get_settings()
    templates_n = int(getattr(settings, "landing_stats_templates", 26) or 26)
    practice_since = int(getattr(settings, "landing_stats_practice_since", 2014) or 2014)
    docs = getattr(settings, "landing_stats_docs", None)
    if docs is None or int(docs) < 0:
        docs_n = 0
        if db is not None:
            try:
                docs_n = int(db.scalar(select(func.count()).select_from(Document)) or 0)
            except Exception:
                docs_n = 0
    else:
        docs_n = int(docs)
    return {
        "templates": templates_n,
        "docs_created": docs_n,
        "practice_since": practice_since,
    }
