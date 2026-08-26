"""Серверное состояние мастера комплекта (не в cookie — иначе step2→step3 теряет selected)."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Request

from app.config import get_settings
from app.security import ensure_session_id

log = logging.getLogger("dok.package_wizard_store")

_SAFE_SID = re.compile(r"^[a-f0-9]{16,64}$")


def _dir() -> Path:
    root = Path(get_settings().files_root) / "_wizard"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for(request: Request) -> Path:
    sid = ensure_session_id(request)
    if not _SAFE_SID.match(str(sid)):
        # на всякий случай не пишем произвольные имена
        sid = re.sub(r"[^a-f0-9]", "", str(sid))[:64] or "anon"
    return _dir() / f"{sid}.json"


def load_wizard(request: Request) -> dict[str, Any]:
    path = _path_for(request)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("wizard load failed %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_wizard(request: Request, data: dict[str, Any]) -> None:
    path = _path_for(request)
    payload = dict(data)
    payload["_saved_at"] = int(time.time())
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.error("wizard save failed %s: %s", path, exc)
        raise
    # cookie оставляем лёгкой: только метка, без core_values/selected
    request.session.pop("package_wizard", None)
    request.session["package_wizard_rev"] = int(request.session.get("package_wizard_rev") or 0) + 1


def clear_wizard(request: Request) -> None:
    path = _path_for(request)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("wizard clear failed %s: %s", path, exc)
    request.session.pop("package_wizard", None)
    request.session.pop("package_wizard_rev", None)
