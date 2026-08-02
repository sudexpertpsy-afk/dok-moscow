"""Ревизия: абсолютные URL из APP/PUBLIC_BASE_URL, не из request.base_url."""

from __future__ import annotations

from app.config import get_settings
from app.hosting import redirect_url_for_path
from app.routers.admin import _invite_link
from app.yandex_oauth import redirect_uri
from starlette.requests import Request


def _req(scheme: str = "http") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "scheme": scheme,
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "query_string": b"",
        }
    )


def test_invite_link_ignores_request_http_scheme(app):
    _client, _ = app
    link = _invite_link(_req("http"), "tok-abc")
    assert link.startswith(get_settings().app_base_url.rstrip("/"))
    assert link.endswith("/invite/tok-abc")
    assert not link.startswith("http://testserver")


def test_yandex_redirect_uri_from_settings():
    uri = redirect_uri()
    assert uri.startswith("https://")
    assert uri.endswith("/auth/yandex/callback")
    assert "app.dok.moscow" in uri or get_settings().app_base_url.rstrip("/") in uri


def test_host_redirect_and_public_canonical_use_env_bases():
    assert redirect_url_for_path("/zakon/").startswith(get_settings().public_base_url.rstrip("/"))
    assert redirect_url_for_path("/login").startswith(get_settings().app_base_url.rstrip("/"))


def test_landing_canonical_from_public_base(app):
    client, _ = app
    r = client.get("/")
    assert r.status_code == 200
    base = get_settings().public_base_url.rstrip("/")
    assert f'rel="canonical" href="{base}/"' in r.text or f'rel="canonical" href="{base}/"' in r.text.replace(
        " />", ">"
    )
    assert 'href="http://testserver' not in r.text
