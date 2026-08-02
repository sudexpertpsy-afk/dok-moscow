"""Точка входа FastAPI — Док.Москва."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import db as dbmod
from app.config import get_settings
from app.db import Base
from app.http_cache import apply_response_cache_headers
from app.models import User, UserRole
from app.routers import (
    admin,
    admin_billing,
    admin_legal,
    admin_templates,
    auth,
    billing,
    cabinet,
    cabinet_billing,
    cabinet_templates,
    calendar,
    counterparties,
    documents,
    form_assist,
    global_search,
    jobs,
    journal,
    landing,
    package,
    party_check,
    staff,
    yandex_auth,
    zakon,
)
from app.routers import settings as settings_routes
from app.security import hash_password
from app.templating import templates

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("dok.access")


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


def _bootstrap_billing() -> None:
    from app.services.billing import bootstrap_billing

    db = dbmod.SessionLocal()
    try:
        bootstrap_billing(db)
    finally:
        db.close()


def _bootstrap_legal() -> None:
    from app.services.legal_registry import bootstrap_legal

    db = dbmod.SessionLocal()
    try:
        bootstrap_legal(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    import os

    from app.billing.jobs import billing_background_loop

    # T1: схема только через Alembic (entrypoint / `alembic upgrade head`).
    # create_all — исключительно при DB_AUTO_CREATE=1 (локальный sqlite без миграций).
    if get_settings().db_auto_create:
        Base.metadata.create_all(bind=dbmod.engine)
    _bootstrap_admin()
    _bootstrap_billing()
    _bootstrap_legal()
    stop = asyncio.Event()
    worker = None
    if os.environ.get("BILLING_WORKER", "1") != "0":
        worker = asyncio.create_task(billing_background_loop(stop), name="billing-worker")
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)

    session_kw: dict = {
        "secret_key": settings.secret_key,
        "session_cookie": settings.session_cookie,
        "max_age": settings.session_max_age,
        "same_site": "lax",
        "https_only": bool(settings.session_https_only),
    }
    # W-26: SESSION_COOKIE_DOMAIN не задаём на проде — cookie только на app-хосте
    if (settings.session_cookie_domain or "").strip():
        session_kw["domain"] = settings.session_cookie_domain.strip()
    app.add_middleware(SessionMiddleware, **session_kw)

    @app.middleware("http")
    async def request_timing(request: Request, call_next):
        """W-32: access-лог и кольцевой буфер латентности."""
        from app.services.ops import record_timing, route_group

        started = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - started) * 1000.0
        path = request.url.path
        if not path.startswith("/static"):
            record_timing(path, ms)
            log.info(
                "%s %s → %s %.1fms group=%s",
                request.method,
                path,
                response.status_code,
                ms,
                route_group(path),
            )
        response.headers["X-Response-Time"] = f"{ms:.1f}ms"
        return response

    @app.middleware("http")
    async def host_routing(request: Request, call_next):
        """W-26: публичные пути ↔ dok.moscow, кабинет ↔ app.dok.moscow."""
        from app.hosting import host_role, path_surface, redirect_url_for_path, request_host

        path = request.url.path
        surface = path_surface(path)
        if surface == "shared":
            response = await call_next(request)
            apply_response_cache_headers(request, response)
            return response
        host = request_host(request.headers.get("host"))
        role = host_role(host)
        if role == "dev":
            response = await call_next(request)
            apply_response_cache_headers(request, response)
            return response
        if role == "public" and surface == "app":
            return RedirectResponse(
                redirect_url_for_path(path, query=request.url.query or ""),
                status_code=301,
            )
        if role == "app" and surface == "public":
            return RedirectResponse(
                redirect_url_for_path(path, query=request.url.query or ""),
                status_code=301,
            )
        response = await call_next(request)
        if role == "app":
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        apply_response_cache_headers(request, response)
        return response

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
    app.include_router(zakon.router)
    app.include_router(auth.router)
    app.include_router(yandex_auth.router)
    app.include_router(billing.router)
    app.include_router(global_search.router)
    app.include_router(cabinet.router)
    app.include_router(cabinet_billing.router)
    app.include_router(cabinet_templates.router)
    app.include_router(staff.router)
    app.include_router(jobs.router)
    app.include_router(documents.router)
    app.include_router(form_assist.router)
    app.include_router(package.router)
    app.include_router(counterparties.router)
    app.include_router(party_check.router)
    app.include_router(journal.router)
    app.include_router(calendar.router)
    app.include_router(settings_routes.router)
    app.include_router(admin.router)
    app.include_router(admin_billing.router)
    app.include_router(admin_legal.router)
    app.include_router(admin_templates.router)

    def _error_context(request: Request, *, status_code: int, detail: str) -> dict:
        from app.hosting import host_role, path_surface, request_host

        s = get_settings()
        role = host_role(request_host(request.headers.get("host")))
        path = request.url.path
        if role == "public":
            surface = "public"
        elif role == "app":
            surface = "app"
        else:
            # dev/testserver: стиль по зоне пути; неизвестный URL → публичная 404
            ps = path_surface(path)
            if ps == "public":
                surface = "public"
            elif path.startswith(
                ("/cabinet", "/admin", "/login", "/api/", "/billing/", "/auth/", "/invite")
            ):
                surface = "app"
            elif status_code == 404:
                surface = "public"
            else:
                surface = "app"
        csrf = ""
        try:
            from app.security import get_csrf_token

            csrf = get_csrf_token(request)
        except Exception:
            csrf = ""
        titles = {
            401: "Требуется вход",
            403: "Доступ ограничен",
            404: "Страница не найдена",
            500: "Внутренняя ошибка сервера",
        }
        return {
            "request": request,
            "app_name": s.app_name,
            "public_base_url": s.public_base_url.rstrip("/"),
            "app_base_url": s.app_base_url.rstrip("/"),
            "yandex_metrika_id": (s.yandex_metrika_id or "").strip(),
            "csrf_token": csrf,
            "status_code": status_code,
            "detail": detail if isinstance(detail, str) and detail else titles.get(status_code, "Ошибка"),
            "error_surface": surface,
        }

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
        if wants_html and exc.status_code in (401, 403, 404, 500):
            detail = exc.detail if isinstance(exc.detail, str) else "Ошибка"
            ctx = _error_context(request, status_code=exc.status_code, detail=detail)
            name = (
                "errors/public.html"
                if ctx["error_surface"] == "public"
                else "errors/app.html"
            )
            return templates.TemplateResponse(
                request,
                name,
                ctx,
                status_code=exc.status_code,
                headers=headers,
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.exception("Unhandled error on %s", request.url.path)
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            ctx = _error_context(request, status_code=500, detail="Внутренняя ошибка сервера")
            name = (
                "errors/public.html"
                if ctx["error_surface"] == "public"
                else "errors/app.html"
            )
            return templates.TemplateResponse(request, name, ctx, status_code=500)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    return app


app = create_app()
