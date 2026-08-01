"""Точка входа FastAPI — Док.Москва."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import db as dbmod
from app.config import get_settings
from app.db import Base
from app.models import User, UserRole
from app.routers import admin, auth, cabinet, counterparties, documents, journal, landing, package
from app.routers import settings as settings_routes
from app.security import hash_password

BASE_DIR = Path(__file__).resolve().parent


def _bootstrap_admin() -> None:
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    email = settings.bootstrap_admin_email.strip().lower()
    db = dbmod.SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            return
        admin_user = User(
            org_id=None,
            email=email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            role=UserRole.service_admin,
            is_active=True,
        )
        db.add(admin_user)
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=dbmod.engine)
    _bootstrap_admin()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=False,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Яндекс.Метрика (скрипт + пиксель) — только при заданном ID на лендинге
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' https://mc.yandex.ru 'unsafe-inline'; "
            "img-src 'self' data: https://mc.yandex.ru; "
            "connect-src 'self' https://mc.yandex.ru; "
            "frame-src https://mc.yandex.ru; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        return response

    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(landing.router)
    app.include_router(auth.router)
    app.include_router(cabinet.router)
    app.include_router(documents.router)
    app.include_router(package.router)
    app.include_router(counterparties.router)
    app.include_router(journal.router)
    app.include_router(settings_routes.router)
    app.include_router(admin.router)

    templates_404 = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        headers = dict(getattr(exc, "headers", None) or {})
        location = headers.get("Location") or headers.get("location")
        # Зависимости (require_org_user и т.п.) могут бросить 303 с Location —
        # отдаём настоящий RedirectResponse, а не JSON с detail.
        if exc.status_code in (301, 302, 303, 307, 308) and location:
            return RedirectResponse(url=location, status_code=exc.status_code, headers=headers)

        accept = request.headers.get("accept", "")
        wants_html = "text/html" in accept
        if exc.status_code == 404 and wants_html:
            s = get_settings()
            return templates_404.TemplateResponse(
                request,
                "landing/404.html",
                {
                    "app_name": s.app_name,
                    "public_base_url": s.public_base_url.rstrip("/"),
                    "app_base_url": s.app_base_url.rstrip("/"),
                    "yandex_metrika_id": (s.yandex_metrika_id or "").strip(),
                    "csrf_token": "",
                },
                status_code=404,
            )
        # Браузерная навигация (в т.ч. hx-boost) — не показывать сырой JSON.
        if wants_html and exc.status_code in (401, 403):
            detail = exc.detail if isinstance(exc.detail, str) else "Ошибка доступа"
            return HTMLResponse(
                content=(
                    "<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
                    f"<title>{exc.status_code}</title></head><body>"
                    f"<p>{detail}</p>"
                    "<p><a href=\"/login\">Войти</a></p>"
                    "</body></html>"
                ),
                status_code=exc.status_code,
                headers=headers,
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)

    return app


app = create_app()
