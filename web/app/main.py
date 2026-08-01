"""Точка входа FastAPI — Док.Москва."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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
        accept = request.headers.get("accept", "")
        if exc.status_code == 404 and "text/html" in accept:
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
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return app


app = create_app()
