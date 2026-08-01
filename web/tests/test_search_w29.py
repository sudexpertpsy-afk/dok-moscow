"""W-29: единый поиск палитры и страницы."""

from __future__ import annotations

from app.deps import CurrentUser
from app.models import Organization, User, UserRole
from app.security import hash_password
from app.services.search import result_to_api_dict, search, search_page
from conftest import login


def _seed(dbmod):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Search Org", requisites={})
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email="search29@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def test_palette_and_page_same_groups(app):
    client, dbmod = app
    uid = _seed(dbmod)
    db = dbmod.SessionLocal()
    try:
        u = db.get(User, uid)
        cu = CurrentUser(
            id=u.id,
            email=u.email,
            org_id=u.org_id,
            role=u.role,
            is_active=u.is_active,
            org_role=u.org_role,
        )
        api = search(db, cu, "документ", limit=8)
        page = search_page(db, cu, "документ", per_group=20).core
        assert [g.key for g in api.groups] == [g.key for g in page.groups]
        for ga, gp in zip(api.groups, page.groups):
            api_titles = {i.title for i in ga.items}
            page_titles = {i.title for i in gp.items}
            assert api_titles <= page_titles
        show = "/cabinet/search?q=dokument"
        payload = result_to_api_dict(api, show_all_url=show)
        assert payload.get("show_all_url")
    finally:
        db.close()


def test_search_page_http(app):
    client, dbmod = app
    _seed(dbmod)
    assert login(client, "search29@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/search?q=настройки")
    assert r.status_code == 200
    assert "Поиск" in r.text
    api = client.get("/api/global-search?q=настройки")
    assert api.status_code == 200
    data = api.json()
    assert "groups" in data
    if not data.get("empty"):
        assert data.get("show_all_url", "").startswith("/cabinet/search")
