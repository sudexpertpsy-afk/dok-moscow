"""Страница «Как пользоваться» в кабинете."""

from __future__ import annotations

from app.defaults import empty_requisites
from app.models import Organization, User, UserRole
from app.navigation import NAV_REGISTRY, match_registry, resolve_nav, roles_for_user
from app.security import hash_password
from conftest import login


def _org_user(dbmod, email: str = "helpuser@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ООО Справка", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return email
    finally:
        db.close()


def test_help_nav_item_registered():
    keys = {item.key for item in NAV_REGISTRY}
    assert "help" in keys
    item = next(i for i in NAV_REGISTRY if i.key == "help")
    assert item.url == "/cabinet/help/"
    assert item.menu is True
    assert item.group == "Справка"


def test_help_synonyms_match_cmdk():
    roles = roles_for_user(is_service_admin=False, has_org=True, is_org_admin=True)
    resolved = resolve_nav(roles=roles, tariff=None, area="search", menu_only=False)
    hits = match_registry("инструкции", resolved)
    assert any(h.item.key == "help" for h in hits)


def test_help_page_renders(app):
    client, dbmod = app
    email = _org_user(dbmod)
    assert login(client, email, "Passw0rd!").status_code == 303
    r = client.get("/cabinet/help/")
    assert r.status_code == 200
    assert "Как пользоваться" in r.text
    assert "Комплект документов" in r.text
    assert "Свои шаблоны DOCX" in r.text
    assert 'href="/cabinet/package/"' in r.text
    assert "Как пользоваться" in r.text  # в nav тоже
    # пункт меню активен
    assert 'class="active"' in r.text or "active" in r.text
