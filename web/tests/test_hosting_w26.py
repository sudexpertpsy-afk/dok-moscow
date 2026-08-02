"""W-26: разделение хостов и SEO."""

from __future__ import annotations

from app.hosting import host_role, path_surface, redirect_url_for_path, request_host


def test_path_surface_matrix():
    assert path_surface("/") == "public"
    assert path_surface("/zakon/foo") == "public"
    assert path_surface("/robots.txt") == "shared"
    assert path_surface("/login") == "app"
    assert path_surface("/cabinet/documents/") == "app"
    assert path_surface("/api/global-search") == "app"
    assert path_surface("/billing/webhook") == "app"
    assert path_surface("/static/app.css") == "shared"


def test_host_role_and_redirect(app):
    client, _ = app
    # TestClient host = testserver → без редиректов (dev)
    r = client.get("/login")
    assert r.status_code == 200

    r = client.get("/login", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert "app.dok.moscow" in r.headers["location"]

    r = client.get("/cabinet/", headers={"Host": "dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert "app.dok.moscow" in r.headers["location"]

    r = client.get("/", headers={"Host": "app.dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"].startswith("https://dok.moscow")

    r = client.get("/zakon/", headers={"Host": "app.dok.moscow"}, follow_redirects=False)
    assert r.status_code == 301


def test_robots_by_host(app):
    client, _ = app
    r = client.get("/robots.txt", headers={"Host": "dok.moscow"})
    assert r.status_code == 200
    assert "Allow: /" in r.text
    assert "Sitemap:" in r.text

    r = client.get("/robots.txt", headers={"Host": "app.dok.moscow"})
    assert r.status_code == 200
    assert "Disallow: /" in r.text
    assert "Allow: /" not in r.text or r.text.strip().startswith("User-agent")


def test_helpers():
    assert request_host("app.dok.moscow:443") == "app.dok.moscow"
    assert host_role("localhost") == "dev"
    assert "app.dok.moscow" in redirect_url_for_path("/login")
    assert "dok.moscow" in redirect_url_for_path("/zakon/")
    # host-hop с каноническим слэшем — без второго slash-редиректа
    assert redirect_url_for_path("/zakon") == "https://dok.moscow/zakon/"
    assert redirect_url_for_path("/cabinet/zakon").endswith("/cabinet/zakon/")
