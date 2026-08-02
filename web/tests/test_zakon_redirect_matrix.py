"""P1: матрица редиректов /zakon и /cabinet/zakon (хост × слэш × auth) — ≤1 hop."""

from __future__ import annotations

import pytest

from app.hosting import canonicalize_directory_path, redirect_url_for_path


def _loc(response) -> str:
    return response.headers.get("location") or ""


def _redirect_hops(client, path: str, *, host: str, max_hops: int = 5) -> list[tuple[str, int, str]]:
    """Следует только Location на том же TestClient (Host из абсолютного URL или исходный)."""
    from urllib.parse import urlparse

    hops: list[tuple[str, int, str]] = []
    current = path
    current_host = host
    for _ in range(max_hops):
        r = client.get(current, headers={"Host": current_host}, follow_redirects=False)
        loc = _loc(r)
        hops.append((f"{current_host}{current}", r.status_code, loc))
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            break
        parsed = urlparse(loc)
        if parsed.scheme and parsed.netloc:
            current_host = parsed.hostname or current_host
            current = parsed.path or "/"
            if parsed.query:
                current += f"?{parsed.query}"
        else:
            current = loc
    return hops


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/zakon", "/zakon/"),
        ("/zakon/", "/zakon/"),
        ("/cabinet/zakon", "/cabinet/zakon/"),
        ("/cabinet/zakon/", "/cabinet/zakon/"),
        ("/zakon/gpk", "/zakon/gpk"),
    ],
)
def test_canonicalize_directory_path(path, expected):
    assert canonicalize_directory_path(path) == expected


def test_host_redirect_includes_trailing_slash():
    assert redirect_url_for_path("/zakon").endswith("/zakon/")
    assert "/zakon/" in redirect_url_for_path("/zakon")
    assert redirect_url_for_path("/cabinet/zakon").endswith("/cabinet/zakon/")
    assert redirect_url_for_path("/zakon/").endswith("/zakon/")


def test_public_zakon_slash_matrix(app):
    client, _ = app

    # /zakon/ на публичном хосте — 200 каталог
    r = client.get("/zakon/", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Законодательство" in r.text or "закон" in r.text.casefold()

    # /zakon → ровно один 301 на /zakon/ (относительный Location)
    r = client.get("/zakon", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r) == "/zakon/"
    assert not _loc(r).startswith("http://")

    hops = _redirect_hops(client, "/zakon", host="dok.moscow")
    assert hops[0][1] == 301
    assert hops[-1][1] == 200
    assert sum(1 for _, code, _ in hops if code in (301, 302, 303, 307, 308)) == 1


def test_app_host_zakon_one_hop_to_public_with_slash(app):
    client, _ = app

    r = client.get("/zakon", headers={"Host": "app.dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    loc = _loc(r)
    assert loc.startswith("https://dok.moscow/zakon")
    assert loc.rstrip("/").endswith("zakon") is False or loc.endswith("/zakon/")
    assert loc.endswith("/zakon/") or loc.endswith("/zakon/?")

    hops = _redirect_hops(client, "/zakon", host="app.dok.moscow")
    redirects = [h for h in hops if h[1] in (301, 302, 303, 307, 308)]
    assert len(redirects) == 1
    assert hops[-1][1] == 200

    r = client.get("/zakon/", headers={"Host": "app.dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r).endswith("/zakon/")


def test_cabinet_zakon_slash_and_host_matrix(app):
    client, _ = app

    # публичный хост → app с каноническим слэшем (один hop)
    r = client.get("/cabinet/zakon", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r) == "https://app.dok.moscow/cabinet/zakon/"

    r = client.get("/cabinet/zakon/", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r) == "https://app.dok.moscow/cabinet/zakon/"

    # app без слэша → один относительный 301, затем auth (401 без сессии)
    r = client.get("/cabinet/zakon", headers={"Host": "app.dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r) == "/cabinet/zakon/"

    hops = _redirect_hops(client, "/cabinet/zakon", host="app.dok.moscow")
    redirects = [h for h in hops if h[1] in (301, 302, 303, 307, 308)]
    assert len(redirects) <= 1
    assert hops[-1][1] in (401, 302, 303)  # неавторизован

    r = client.get("/cabinet/zakon/", headers={"Host": "app.dok.moscow"}, follow_redirects=False)
    assert r.status_code in (401, 302, 303)
    assert r.status_code not in (301, 307, 308)


def test_cabinet_zakon_works_when_authed(app):
    from datetime import timedelta

    from sqlalchemy import select

    from app.defaults import empty_requisites
    from app.models import (
        Organization,
        Subscription,
        SubscriptionPeriod,
        SubscriptionStatus,
        Tariff,
        TariffCode,
        User,
        UserRole,
        utcnow,
    )
    from app.security import hash_password
    from app.services.billing import ensure_tariffs
    from conftest import login

    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="Org zakon redirect", requisites=empty_requisites())
        db.add(org)
        db.flush()
        email = "zakon-redir@example.com"
        db.add(
            User(
                org_id=org.id,
                email=email,
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        t = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        if t is not None:
            db.add(
                Subscription(
                    org_id=org.id,
                    tariff_id=t.id,
                    period=SubscriptionPeriod.month,
                    starts_at=utcnow() - timedelta(days=1),
                    ends_at=utcnow() + timedelta(days=30),
                    status=SubscriptionStatus.active,
                    auto_renew=False,
                    is_beta=False,
                )
            )
        db.commit()
    finally:
        db.close()

    assert login(client, email, "Passw0rd!").status_code in (302, 303)

    r = client.get("/cabinet/zakon/")
    assert r.status_code == 200
    assert "закон" in r.text.casefold() or "норматив" in r.text.casefold()

    r = client.get("/cabinet/zakon", follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r) == "/cabinet/zakon/"


def test_query_preserved_on_slash_redirect(app):
    client, _ = app
    r = client.get("/zakon?q=тест", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r).startswith("/zakon/?")
    assert "q=" in _loc(r)


def test_head_zakon_slash_redirect(app):
    """curl -sIL шлёт HEAD — редирект без слэша должен отвечать 301, не 405."""
    client, _ = app
    r = client.head("/zakon", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert _loc(r) == "/zakon/"
