"""Разделение публичного и кабинетного хостов (W-26)."""

from __future__ import annotations

from urllib.parse import urlparse

from app.config import Settings, get_settings

# Префиксы / точные пути кабинета и auth — только app-хост
_APP_PREFIXES = (
    "/login",
    "/logout",
    "/forgot-password",
    "/reset-password",
    "/invite",
    "/auth/",
    "/cabinet",
    "/admin",
    "/api/",
    "/billing/",
)

# Публичные пути — только dok.moscow (кроме static)
_PUBLIC_EXACT = frozenset(
    {
        "/",
        "/apply",
        "/privacy",
        "/offer",
        "/requisites",
        "/tariffs",
        "/contacts",
        "/sitemap.xml",
    }
)
_PUBLIC_PREFIXES = ("/zakon",)

# Каталоги с trailing-slash роутами: без слэша → один 301, не второй hop после host-редиректа.
_DIRECTORY_INDEX_PATHS = frozenset({"/zakon", "/cabinet/zakon"})


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def public_host(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return _hostname(settings.public_base_url) or "dok.moscow"


def app_host(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return _hostname(settings.app_base_url) or "app.dok.moscow"


def request_host(host_header: str | None) -> str:
    raw = (host_header or "").split(",")[0].strip().lower()
    if ":" in raw and not raw.startswith("["):
        # host:port
        raw = raw.rsplit(":", 1)[0]
    return raw


def is_dev_host(host: str) -> bool:
    h = host.lower()
    return h in {"testserver", "localhost", "127.0.0.1", "0.0.0.0", ""} or h.endswith(".local")


def path_surface(path: str) -> str:
    """'public' | 'app' | 'shared'."""
    if path.startswith("/static") or path in {
        "/favicon.ico",
        "/favicon.svg",
        "/favicon.png",
        "/robots.txt",  # разный текст на каждом хосте
    }:
        return "shared"
    if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES):
        return "public"
    if path.startswith(_APP_PREFIXES) or path in {"/login", "/logout"}:
        return "app"
    # прочее (в т.ч. неизвестное) — app, чтобы не светить кабинет на лендинге
    return "app"


def host_role(host: str, settings: Settings | None = None) -> str:
    """'public' | 'app' | 'dev'."""
    if is_dev_host(host):
        return "dev"
    settings = settings or get_settings()
    pub = public_host(settings)
    app = app_host(settings)
    if host == pub or host == f"www.{pub}":
        return "public"
    if host == app:
        return "app"
    # неизвестный хост — не ломаем (прокси/IP)
    return "dev"


def canonicalize_directory_path(path: str) -> str:
    """Для index-каталогов добавить trailing slash (host + slash = один hop)."""
    if path in _DIRECTORY_INDEX_PATHS:
        return f"{path}/"
    return path


def redirect_url_for_path(
    path: str,
    *,
    query: str = "",
    settings: Settings | None = None,
) -> str:
    """Абсолютный URL правильного хоста для path."""
    settings = settings or get_settings()
    path = canonicalize_directory_path(path)
    surface = path_surface(path)
    base = settings.public_base_url if surface == "public" else settings.app_base_url
    base = base.rstrip("/")
    q = f"?{query}" if query else ""
    return f"{base}{path}{q}"


def public_url(path: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return f"{settings.public_base_url.rstrip('/')}{path}"
