"""Cache-Control и Vary для HTML/HTMX (W-31)."""

from __future__ import annotations

import time

from starlette.requests import Request
from starlette.responses import Response

from app.hosting import path_surface

_PUBLIC_CACHE_PURGED_AT: float = 0.0
_PURGE_TTL_SEC = 300.0


def purge_public_cache() -> None:
    """Пометить публичный HTML как недавно сброшенный для CDN/browser revalidate."""
    global _PUBLIC_CACHE_PURGED_AT
    _PUBLIC_CACHE_PURGED_AT = time.monotonic()


def public_cache_recently_purged(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    return _PUBLIC_CACHE_PURGED_AT > 0 and now - _PUBLIC_CACHE_PURGED_AT <= _PURGE_TTL_SEC


def apply_response_cache_headers(request: Request, response: Response) -> None:
    path = request.url.path
    if path.startswith("/static"):
        return
    # HTMX: разные тела для полного и частичного ответа
    if request.headers.get("hx-request") or response.headers.get("HX-Request"):
        existing = response.headers.get("Vary", "")
        parts = [p.strip() for p in existing.split(",") if p.strip()]
        if "HX-Request" not in parts:
            parts.append("HX-Request")
        response.headers["Vary"] = ", ".join(parts)

    surface = path_surface(path)
    if surface == "app" or path.startswith("/api/") or path.startswith("/billing/"):
        response.headers.setdefault("Cache-Control", "no-store")
        return
    if surface == "public":
        # Лендинг и /zakon — короткий публичный кэш
        ct = (response.headers.get("content-type") or "").lower()
        if "text/html" in ct or response.status_code in (301, 302, 303, 307, 308):
            if public_cache_recently_purged():
                response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
                return
            response.headers.setdefault("Cache-Control", "public, max-age=300")
