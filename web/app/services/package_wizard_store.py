"""Серверное состояние мастера комплекта в БД (W-46 §4).

Интерфейс load/save/clear(request) сохранён; файловый бэкенд FILES_ROOT/_wizard
читается один раз при миграции строки и затем удаляется.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import delete

from app.config import get_settings
from app.models import WizardSession, utcnow
from app.security import ensure_session_id

log = logging.getLogger("dok.package_wizard_store")

_SAFE_SID = re.compile(r"^[a-f0-9]{16,64}$")
TTL = timedelta(hours=24)


def _sid(request: Request) -> str:
    sid = ensure_session_id(request)
    if not _SAFE_SID.match(str(sid)):
        sid = re.sub(r"[^a-f0-9]", "", str(sid))[:64] or "anon"
    return str(sid)


def _legacy_path(sid: str) -> Path:
    root = Path(get_settings().files_root) / "_wizard"
    return root / f"{sid}.json"


def _session_user_ids(request: Request) -> tuple[int | None, int | None]:
    org_id = request.session.get("org_id")
    user_id = request.session.get("user_id")
    try:
        org_id = int(org_id) if org_id is not None else None
    except (TypeError, ValueError):
        org_id = None
    try:
        user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    return org_id, user_id


def _import_legacy(sid: str) -> dict[str, Any] | None:
    path = _legacy_path(sid)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("wizard legacy load failed %s: %s", path, exc)
        return None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return data if isinstance(data, dict) else None


def load_wizard(request: Request) -> dict[str, Any]:
    from app.db import SessionLocal

    sid = _sid(request)
    now = utcnow()
    try:
        with SessionLocal() as db:
            row = db.get(WizardSession, sid)
            if row is not None:
                exp = row.expires_at
                if exp.tzinfo is None:
                    from datetime import timezone as _tz

                    exp = exp.replace(tzinfo=_tz.utc)
                if exp < now:
                    db.delete(row)
                    db.commit()
                    return {}
                return dict(row.state or {})
            legacy = _import_legacy(sid)
            if legacy is None:
                return {}
            org_id, user_id = _session_user_ids(request)
            db.add(
                WizardSession(
                    sid=sid,
                    org_id=org_id,
                    user_id=user_id,
                    state=legacy,
                    updated_at=now,
                    expires_at=now + TTL,
                )
            )
            db.commit()
            return dict(legacy)
    except Exception:
        log.exception("wizard load failed sid=%s", sid)
        return {}


def save_wizard(request: Request, data: dict[str, Any]) -> None:
    from app.db import SessionLocal

    sid = _sid(request)
    now = utcnow()
    payload = dict(data)
    payload["_saved_at"] = int(time.time())
    org_id, user_id = _session_user_ids(request)
    try:
        with SessionLocal() as db:
            row = db.get(WizardSession, sid)
            if row is None:
                row = WizardSession(
                    sid=sid,
                    org_id=org_id,
                    user_id=user_id,
                    state=payload,
                    updated_at=now,
                    expires_at=now + TTL,
                )
                db.add(row)
            else:
                row.state = payload
                row.updated_at = now
                row.expires_at = now + TTL
                if org_id is not None:
                    row.org_id = org_id
                if user_id is not None:
                    row.user_id = user_id
            db.commit()
    except Exception:
        log.exception("wizard save failed sid=%s", sid)
        raise
    request.session.pop("package_wizard", None)
    request.session["package_wizard_rev"] = int(request.session.get("package_wizard_rev") or 0) + 1


def clear_wizard(request: Request) -> None:
    from app.db import SessionLocal

    sid = _sid(request)
    try:
        with SessionLocal() as db:
            row = db.get(WizardSession, sid)
            if row is not None:
                db.delete(row)
                db.commit()
    except Exception:
        log.exception("wizard clear failed sid=%s", sid)
    try:
        _legacy_path(sid).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("wizard legacy clear failed: %s", exc)
    request.session.pop("package_wizard", None)
    request.session.pop("package_wizard_rev", None)


def purge_expired_wizard_sessions(db) -> int:
    """Удалить истёкшие сессии. Возвращает число строк."""
    now = utcnow()
    result = db.execute(delete(WizardSession).where(WizardSession.expires_at < now))
    return int(result.rowcount or 0)
