"""Публичное демо ЕГРЮЛ для лендинга (без регистрации)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.rate_limit import LoginRateLimiter
from app.services.landing_demo import find_party_public, party_to_demo_json

router = APIRouter(prefix="/api/demo", tags=["demo"])

# 5 запросов / IP / час — как в ТЗ
demo_limiter = LoginRateLimiter(limit=5, window_sec=60 * 60, name="demo_egrul")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@router.get("/egrul")
def demo_egrul(request: Request, inn: str = ""):
    ip = _client_ip(request)
    if demo_limiter.is_blocked(ip):
        return JSONResponse(
            {"ok": False, "error": "rate_limit", "fallback": True},
            status_code=429,
        )
    digits = "".join(ch for ch in (inn or "") if ch.isdigit())
    if len(digits) not in (10, 12):
        return JSONResponse({"ok": False, "error": "bad_inn"}, status_code=400)

    demo_limiter.register_failure(ip)
    card = find_party_public(digits)
    if card is None:
        return JSONResponse(
            {"ok": False, "error": "not_found", "fallback": True},
            status_code=404,
        )
    return {"ok": True, "party": party_to_demo_json(card)}
