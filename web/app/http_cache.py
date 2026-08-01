"""Cache-Control и Vary для HTML/HTMX (W-31)."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from app.hosting import path_surface


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
            response.headers.setdefault("Cache-Control", "public, max-age=300")
