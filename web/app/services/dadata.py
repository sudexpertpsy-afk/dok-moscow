"""Серверный прокси DaData: party / address / bank, кэш и суточный лимит."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event

DADATA_BASE = "https://suggestions.dadata.ru/suggestions/api/4_1/rs"


@dataclass
class SuggestItem:
    value: str
    data: dict


class _Cache:
    def __init__(self, ttl_sec: int = 3600) -> None:
        self.ttl = ttl_sec
        self._data: dict[str, tuple[float, list[SuggestItem]]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> list[SuggestItem] | None:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            ts, items = hit
            if now - ts > self.ttl:
                self._data.pop(key, None)
                return None
            return items

    def set(self, key: str, items: list[SuggestItem]) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), items)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _Cache()


def clear_dadata_cache() -> None:
    _cache.clear()


def _today_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def usage_today(db: Session, org_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.org_id == org_id,
                Event.type == "dadata_suggest",
                Event.ts >= _today_start(),
            )
        )
        or 0
    )


def _record_usage(db: Session, org_id: int, user_id: int | None, kind: str, query: str) -> None:
    db.add(
        Event(
            org_id=org_id,
            user_id=user_id,
            type="dadata_suggest",
            details={"kind": kind, "q": query[:80]},
        )
    )
    db.commit()


def _headers() -> dict[str, str] | None:
    key = (get_settings().dadata_key or "").strip()
    if not key:
        return None
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {key}",
    }


def _request(path: str, body: dict) -> list[dict]:
    headers = _headers()
    if headers is None:
        return []
    url = f"{DADATA_BASE}/{path.lstrip('/')}"
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=8.0)
    except httpx.HTTPError:
        return []
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    return list(payload.get("suggestions") or [])


def suggest(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    kind: str,
    query: str,
    count: int = 7,
) -> list[SuggestItem]:
    """Подсказки DaData. Без ключа/сети — пустой список (мягкая деградация)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []

    settings = get_settings()
    if usage_today(db, org_id) >= settings.dadata_daily_limit:
        return []

    cache_key = f"{kind}:{q.lower()}:{count}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    path_map = {
        "party": "suggest/party",
        "address": "suggest/address",
        "bank": "suggest/bank",
    }
    path = path_map.get(kind)
    if not path:
        return []

    raw = _request(path, {"query": q, "count": count})
    items = [SuggestItem(value=str(s.get("value") or ""), data=dict(s.get("data") or {})) for s in raw]
    _cache.set(cache_key, items)
    if items and _headers() is not None:
        _record_usage(db, org_id, user_id, kind, q)
    return items


def party_to_counterparty_fields(item: SuggestItem) -> dict:
    """Разобрать suggest/party → поля карточки ЮЛ."""
    data = item.data or {}
    addr = data.get("address") or {}
    addr_value = ""
    if isinstance(addr, dict):
        addr_value = str(addr.get("unrestricted_value") or addr.get("value") or "")
    management = data.get("management") or {}
    name = data.get("name") or {}
    return {
        "name": str(name.get("full_with_opf") or name.get("short_with_opf") or item.value or ""),
        "inn": str(data.get("inn") or ""),
        "kpp": str(data.get("kpp") or ""),
        "ogrn": str(data.get("ogrn") or ""),
        "address": addr_value,
        "fio": str(management.get("name") or ""),
        "source": "dadata",
    }


def bank_to_fields(item: SuggestItem) -> dict:
    data = item.data or {}
    return {
        "bank_name": str(data.get("name", {}).get("payment") or item.value or "")
        if isinstance(data.get("name"), dict)
        else str(item.value or ""),
        "bank_bik": str(data.get("bic") or ""),
        "bank_corr_account": str(data.get("correspondent_account") or ""),
    }
