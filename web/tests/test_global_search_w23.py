"""W-23: глобальный поиск и реестр навигации."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.defaults import empty_requisites
from app.main import create_app
from app.models import (
    Counterparty,
    CounterpartySource,
    CounterpartyType,
    Organization,
    Subscription,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.navigation import (
    NAV_REGISTRY,
    ROUTE_EXCEPTIONS,
    match_registry,
    registry_urls,
    resolve_nav,
    roles_for_user,
)
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from conftest import csrf_from, login


def _org_user(dbmod, email: str, *, tariff: TariffCode = TariffCode.specialist):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name=f"Org-{email}", requisites=empty_requisites())
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
        ensure_beta_subscriptions(db)
        t = db.scalar(select(Tariff).where(Tariff.code == tariff))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        sub.tariff_id = t.id
        sub.status = SubscriptionStatus.active
        sub.ends_at = utcnow() + timedelta(days=30)
        sub.is_beta = False
        db.commit()
        return org.id, email
    finally:
        db.close()


def _iter_route_paths(routes) -> list[str]:
    """Обойти вложенные APIRouter (Starlette _IncludedRouter без .path)."""
    out: list[str] = []
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            out.append(path)
            continue
        inner = getattr(route, "app", None)
        nested = getattr(inner, "routes", None) if inner is not None else None
        if nested is None:
            nested = getattr(route, "routes", None)
        if nested:
            out.extend(_iter_route_paths(nested))
    return out


def test_registry_covers_app_routes():
    """Каждый роут приложения — в реестре или в явном списке исключений."""
    app = create_app()
    registered = registry_urls()
    uncovered: list[str] = []
    for path in _iter_route_paths(app.routes):
        if path.startswith("/static"):
            continue
        candidates = {path, path.rstrip("/") or "/", path + "/" if not path.endswith("/") else path}
        if candidates & registered:
            continue
        if path in ROUTE_EXCEPTIONS:
            continue
        uncovered.append(path)
    assert uncovered == [], f"Маршруты вне реестра/исключений: {uncovered}"


def test_synonyms_kassa_and_pko():
    roles = roles_for_user(is_service_admin=True, has_org=False)
    resolved = resolve_nav(
        roles=roles, tariff=None, area="search", include_upsell=False, include_quick=True
    )
    hits = match_registry("касса", resolved)
    assert hits and hits[0].item.key == "admin_paysettings"
    assert "Платёжная система" in hits[0].title

    roles_org = roles_for_user(is_service_admin=False, has_org=True)
    resolved_org = resolve_nav(
        roles=roles_org,
        tariff=TariffCode.organization,
        area="search",
        include_upsell=True,
        include_quick=True,
    )
    # пко — синоним шаблонов админки; для org — документы/комплект
    hits_pko_admin = match_registry("пко", resolved)
    assert any("пко" in " ".join(h.item.synonyms) for h in hits_pko_admin)


def test_guest_upsell_in_search(app):
    client, dbmod = app
    _org_user(dbmod, "guest-gs@example.com", tariff=TariffCode.guest)
    assert login(client, "guest-gs@example.com", "Passw0rd!").status_code == 303
    r = client.get("/api/global-search", params={"q": "проверк"})
    assert r.status_code == 200
    data = r.json()
    # раздел проверки виден с пометкой тарифа
    flat = [i for g in data["groups"] for i in g["items"]]
    party = [i for i in flat if "Проверка" in i["title"] or "провер" in i["title"].casefold()]
    assert party, data
    assert any(i.get("upsell") or "тарифе" in (i.get("badge") or "") for i in party)


def test_org_isolation_in_global_search(app):
    client, dbmod = app
    org_a, email_a = _org_user(dbmod, "a-gs@example.com")
    org_b, email_b = _org_user(dbmod, "b-gs@example.com")

    db = dbmod.SessionLocal()
    try:
        db.add(
            Counterparty(
                org_id=org_a,
                type=CounterpartyType.ul,
                name="СекретнаяАльфа",
                inn="7707083893",
                source=CounterpartySource.manual,
            )
        )
        db.add(
            Counterparty(
                org_id=org_b,
                type=CounterpartyType.ul,
                name="СекретнаяБета",
                inn="7736207543",
                source=CounterpartySource.manual,
            )
        )
        db.commit()
    finally:
        db.close()

    assert login(client, email_a, "Passw0rd!").status_code == 303
    r = client.get("/api/global-search", params={"q": "Секретная"})
    assert r.status_code == 200
    blob = r.text
    assert "СекретнаяАльфа" in blob
    assert "СекретнаяБета" not in blob


def test_service_admin_sees_leads_group(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/api/global-search", params={"q": "касса"})
    assert r.status_code == 200
    data = r.json()
    titles = [i["title"] for g in data["groups"] for i in g["items"]]
    assert any("Платёжная" in t for t in titles)


def test_empty_result_has_sitemap_hint(app):
    client, dbmod = app
    _org_user(dbmod, "empty-gs@example.com")
    assert login(client, "empty-gs@example.com", "Passw0rd!").status_code == 303
    r = client.get("/api/global-search", params={"q": "zzzzнесуществующийзапрос999"})
    assert r.status_code == 200
    data = r.json()
    assert data["empty"] is True
    assert "Не нашлось" in data["hint"]
    assert data["sitemap"]


def test_quick_actions_present(app):
    client, dbmod = app
    _org_user(dbmod, "qa-gs@example.com")
    assert login(client, "qa-gs@example.com", "Passw0rd!").status_code == 303
    r = client.get("/api/global-search", params={"q": "новый комплект"})
    assert r.status_code == 200
    data = r.json()
    actions = [g for g in data["groups"] if g["key"] == "actions"]
    assert actions
    assert any("комплект" in i["title"].casefold() for i in actions[0]["items"])


def test_cabinet_menu_from_registry(app):
    client, dbmod = app
    _org_user(dbmod, "menu-gs@example.com", tariff=TariffCode.organization)
    assert login(client, "menu-gs@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/documents/")
    assert r.status_code == 200
    assert "Проверка контрагента" in r.text
    assert 'data-gs-open' in r.text
    assert "/static/global-search.js" in r.text


def test_nav_registry_has_unique_keys():
    keys = [i.key for i in NAV_REGISTRY]
    assert len(keys) == len(set(keys))
