"""Серверный прокси DaData: party / address / bank, findById, кэш и суточный лимит."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event

DADATA_BASE = "https://suggestions.dadata.ru/suggestions/api/4_1/rs"

STATUS_META: dict[str, tuple[str, str]] = {
    "ACTIVE": ("действует", "ok"),
    "LIQUIDATING": ("ликвидируется", "warn"),
    "LIQUIDATED": ("ликвидирована", "danger"),
    "BANKRUPT": ("банкротство", "danger"),
    "REORGANIZING": ("реорганизация", "warn"),
}


@dataclass
class SuggestItem:
    value: str
    data: dict


@dataclass
class PartyCard:
    """Нормализованная карточка ЮЛ/ИП из findById/party."""

    raw: dict = field(default_factory=dict)
    party_type: str = ""  # LEGAL / INDIVIDUAL
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    name_full: str = ""
    name_short: str = ""
    status: str = ""
    status_label: str = ""
    status_tone: str = "muted"
    registration_date: str = ""
    liquidation_date: str = ""
    address: str = ""
    management_post: str = ""
    management_name: str = ""
    management_start: str = ""
    capital: str = ""
    okved_main: str = ""
    okved_main_name: str = ""
    okved_extra: list[str] = field(default_factory=list)
    employee_count: str = ""
    tax_office: str = ""
    branches: list[str] = field(default_factory=list)
    # ИП
    fio: str = ""
    ogrnip: str = ""

    def display_rows(self) -> list[tuple[str, str]]:
        """Пары (подпись, значение) — только непустые поля."""
        rows: list[tuple[str, str]] = []

        def add(label: str, value: str | None) -> None:
            v = (value or "").strip()
            if v:
                rows.append((label, v))

        if self.party_type == "INDIVIDUAL":
            add("ФИО", self.fio or self.name_full or self.name_short)
            add("ОГРНИП", self.ogrnip or self.ogrn)
            add("ИНН", self.inn)
            add("Статус", self.status_label or self.status)
            add("Дата регистрации", self.registration_date)
            add("Дата ликвидации", self.liquidation_date)
            add("Адрес", self.address)
            add("ОКВЭД основной", _join_okved(self.okved_main, self.okved_main_name))
            if self.okved_extra:
                add("ОКВЭД дополнительные", "; ".join(self.okved_extra))
            add("Налоговый орган", self.tax_office)
            return rows

        add("Полное наименование", self.name_full)
        add("Краткое наименование", self.name_short)
        add("Статус", self.status_label or self.status)
        add("Дата регистрации", self.registration_date)
        add("Дата ликвидации", self.liquidation_date)
        add("ИНН", self.inn)
        add("КПП", self.kpp)
        add("ОГРН", self.ogrn)
        add("Юридический адрес", self.address)
        mgr = " ".join(x for x in (self.management_post, self.management_name) if x).strip()
        add("Руководитель", mgr)
        add("Дата вступления в должность", self.management_start)
        add("Уставный капитал", self.capital)
        add("ОКВЭД основной", _join_okved(self.okved_main, self.okved_main_name))
        if self.okved_extra:
            add("ОКВЭД дополнительные", "; ".join(self.okved_extra))
        add("Численность", self.employee_count)
        add("Налоговый орган", self.tax_office)
        if self.branches:
            add("Филиалы", "; ".join(self.branches))
        return rows


def _join_okved(code: str, name: str) -> str:
    code = (code or "").strip()
    name = (name or "").strip()
    if code and name:
        return f"{code} — {name}"
    return code or name


class _Cache:
    def __init__(self, ttl_sec: int = 3600) -> None:
        self.ttl = ttl_sec
        self._data: dict[str, tuple[float, list[SuggestItem]]] = {}
        self._party: dict[str, tuple[float, PartyCard | None]] = {}
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

    def get_party(self, key: str) -> tuple[bool, PartyCard | None]:
        now = time.monotonic()
        with self._lock:
            hit = self._party.get(key)
            if not hit:
                return False, None
            ts, card = hit
            if now - ts > self.ttl:
                self._party.pop(key, None)
                return False, None
            return True, card

    def set_party(self, key: str, card: PartyCard | None) -> None:
        with self._lock:
            self._party[key] = (time.monotonic(), card)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._party.clear()


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


def party_check_usage_today(db: Session, org_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.org_id == org_id,
                Event.type == "dadata_party_check",
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


def _record_party_usage(
    db: Session, org_id: int, user_id: int | None, query: str, inn: str
) -> None:
    db.add(
        Event(
            org_id=org_id,
            user_id=user_id,
            type="dadata_party_check",
            details={"q": query[:80], "inn": inn[:12]},
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
        # Подсказки должны отвечать быстро; 8s давало ощущение «зависания».
        response = httpx.post(url, json=body, headers=headers, timeout=3.0)
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

    cache_key = f"{kind}:{q.lower()}:{count}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    settings = get_settings()
    if usage_today(db, org_id) >= settings.dadata_daily_limit:
        return []

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


def _ms_to_date(value: Any) -> str:
    if value in (None, "", 0):
        return ""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return str(value)
    if ms <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return dt.strftime("%d.%m.%Y")


def _format_capital(data: dict) -> str:
    capital = data.get("capital") or {}
    if not isinstance(capital, dict):
        return ""
    value = capital.get("value")
    if value in (None, ""):
        return ""
    try:
        num = float(value)
        formatted = f"{num:,.2f}".replace(",", " ").replace(".", ",")
        if formatted.endswith(",00"):
            formatted = formatted[:-3]
    except (TypeError, ValueError):
        formatted = str(value)
    unit = str(capital.get("type") or "руб.")
    return f"{formatted} {unit}".strip()


def parse_party_suggestion(raw: dict) -> PartyCard | None:
    """Разобрать элемент suggestions[] → PartyCard."""
    if not raw:
        return None
    data = dict(raw.get("data") or {})
    if not data and not raw.get("value"):
        return None

    state = data.get("state") or {}
    status = str(state.get("status") or "").upper()
    label, tone = STATUS_META.get(status, (status.lower() or "неизвестно", "muted"))

    name = data.get("name") or {}
    if not isinstance(name, dict):
        name = {}
    addr = data.get("address") or {}
    addr_value = ""
    if isinstance(addr, dict):
        addr_value = str(addr.get("unrestricted_value") or addr.get("value") or "")
    elif isinstance(addr, str):
        addr_value = addr

    management = data.get("management") or {}
    if not isinstance(management, dict):
        management = {}

    okved = data.get("okved") or ""
    okved_type = data.get("okved_type") or ""
    okved_name = ""
    # В платном тарифе часто есть okveds[]
    okveds = data.get("okveds") or []
    okved_extra: list[str] = []
    if isinstance(okveds, list):
        for item in okveds:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            nm = str(item.get("name") or "").strip()
            main = bool(item.get("main"))
            line = _join_okved(code, nm)
            if not line:
                continue
            if main and not okved:
                okved = code
                okved_name = nm
            elif main:
                okved_name = okved_name or nm
            else:
                okved_extra.append(line)

    finance = data.get("finance") or {}
    employee = ""
    if isinstance(finance, dict) and finance.get("employee_count") not in (None, ""):
        employee = str(finance.get("employee_count"))
    if not employee and data.get("employee_count") not in (None, ""):
        employee = str(data.get("employee_count"))

    authorities = data.get("authorities") or {}
    tax_office = ""
    if isinstance(authorities, dict):
        fts = authorities.get("fts_registration") or authorities.get("fts_report") or {}
        if isinstance(fts, dict):
            tax_office = str(fts.get("name") or fts.get("type") or "")
            code = str(fts.get("code") or "")
            if code and tax_office:
                tax_office = f"{code} {tax_office}"
            elif code:
                tax_office = code

    branches: list[str] = []
    for key in ("branch_type",):
        _ = key
    raw_branches = data.get("branches") or data.get("phones")  # phones не филиалы
    # DaData: иногда филиалы в отдельном запросе; в карточке компании — address + branch_count
    branch_count = data.get("branch_count")
    if data.get("branch_type") == "MAIN" and branch_count not in (None, "", 0):
        branches.append(f"филиалов: {branch_count}")
    # Список дочерних подразделений, если API отдал
    for item in data.get("managers") or []:
        pass
    extras = data.get("documents") or {}

    party_type = str(data.get("type") or "").upper()
    fio_obj = data.get("fio") or {}
    fio = ""
    if isinstance(fio_obj, dict):
        parts = [fio_obj.get("surname"), fio_obj.get("name"), fio_obj.get("patronymic")]
        fio = " ".join(str(p) for p in parts if p)
    if not fio and party_type == "INDIVIDUAL":
        fio = str(name.get("full") or name.get("short") or raw.get("value") or "")

    card = PartyCard(
        raw=dict(raw),
        party_type=party_type,
        inn=str(data.get("inn") or ""),
        kpp=str(data.get("kpp") or ""),
        ogrn=str(data.get("ogrn") or ""),
        name_full=str(name.get("full_with_opf") or name.get("full") or ""),
        name_short=str(name.get("short_with_opf") or name.get("short") or ""),
        status=status,
        status_label=label,
        status_tone=tone,
        registration_date=_ms_to_date(state.get("registration_date")),
        liquidation_date=_ms_to_date(state.get("liquidation_date")),
        address=addr_value,
        management_post=str(management.get("post") or ""),
        management_name=str(management.get("name") or ""),
        management_start=_ms_to_date(management.get("start_date")),
        capital=_format_capital(data),
        okved_main=str(okved or ""),
        okved_main_name=str(okved_name or ""),
        okved_extra=okved_extra,
        employee_count=employee,
        tax_office=tax_office,
        branches=branches,
        fio=fio,
        ogrnip=str(data.get("ogrn") or "") if party_type == "INDIVIDUAL" else "",
    )
    # Убрать шум неиспользуемых локальных
    _ = (okved_type, extras, raw_branches)
    return card


def find_party(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    query: str,
) -> PartyCard | None:
    """Точная карточка по ИНН/ОГРН (findById/party). Учитывает лимит проверок через Event."""
    q = (query or "").strip()
    if not q:
        return None

    cache_key = f"find:{q}"
    hit, cached = _cache.get_party(cache_key)
    if hit:
        if cached is not None:
            _record_party_usage(db, org_id, user_id, q, cached.inn or q)
        return cached

    if _headers() is None:
        return None

    raw_list = _request("findById/party", {"query": q})
    if not raw_list:
        _cache.set_party(cache_key, None)
        return None

    card = parse_party_suggestion(raw_list[0])
    _cache.set_party(cache_key, card)
    if card is not None:
        _record_party_usage(db, org_id, user_id, q, card.inn or q)
    return card


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


def party_card_to_counterparty_fields(card: PartyCard) -> dict:
    if card.party_type == "INDIVIDUAL":
        return {
            "name": None,
            "fio": card.fio or card.name_full or card.name_short or None,
            "inn": card.inn or None,
            "kpp": None,
            "ogrn": card.ogrnip or card.ogrn or None,
            "address": card.address or None,
        }
    return {
        "name": card.name_full or card.name_short or None,
        "fio": card.management_name or None,
        "inn": card.inn or None,
        "kpp": card.kpp or None,
        "ogrn": card.ogrn or None,
        "address": card.address or None,
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
