"""W-49 B: шаблоны номеров, паритет peek/allocate, онбординг-чеклист."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Counter,
    Counterparty,
    CounterpartySource,
    CounterpartyType,
    Document,
    DocumentFormat,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.counters import allocate_number, peek_number
from app.services.numbering import (
    DEFAULT_TEMPLATE,
    NumberingError,
    render_number,
    validate_template,
)
from app.services.onboarding import (
    build_onboarding_checklist,
    confirm_numbering_defaults,
    dismiss_onboarding,
    update_counter_numbering,
)
from conftest import csrf_from, login


def _seed_org(dbmod, email: str = "w49b@example.com", *, with_requisites: bool = False):
    db = dbmod.SessionLocal()
    try:
        req = empty_requisites()
        if with_requisites:
            req["организация"]["короткое_название"] = "ООО Тест"
            req["организация"]["инн"] = "7707083893"
        org = Organization(name="W49B", requisites=req)
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
        return org.id, email
    finally:
        db.close()


def test_render_legacy_byte_for_byte():
    assert render_number(None, n=1, prefix="Д-", suffix="") == "Д-1"
    assert render_number(None, n=48, prefix="Д-", suffix="/26") == "Д-48/26"
    assert render_number(DEFAULT_TEMPLATE, n=48, prefix="Д-", suffix="/26") == "Д-48/26"
    assert render_number("", n=7, prefix="", suffix="") == "7"


def test_render_tokens():
    on = date(2026, 3, 15)
    assert (
        render_number("{префикс}-{гггг}/{nnn}", n=48, prefix="Д", on=on) == "Д-2026/048"
    )
    assert render_number("{префикс}{nn}", n=5, prefix="А-") == "А-05"
    assert render_number("{n}/{гг}", n=12, on=on) == "12/26"


def test_validate_template_requires_n():
    assert validate_template("") == DEFAULT_TEMPLATE
    try:
        validate_template("{префикс}-{гггг}")
        raise AssertionError("expected NumberingError")
    except NumberingError:
        pass
    try:
        validate_template("{foo}{n}")
        raise AssertionError("expected NumberingError")
    except NumberingError:
        pass


def test_peek_allocate_parity_legacy(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "parity-leg@example.com")
    db = dbmod.SessionLocal()
    try:
        peek = peek_number(db, org_id, "dogovor", prefix="Д-", suffix="/26")
        n, formatted = allocate_number(db, org_id, "dogovor", prefix="Д-", suffix="/26")
        db.commit()
        assert n == 1
        assert formatted == peek == "Д-1/26"
    finally:
        db.close()


def test_peek_allocate_parity_custom_template(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "parity-tmpl@example.com")
    db = dbmod.SessionLocal()
    try:
        on = date(2026, 6, 1)
        update_counter_numbering(
            db,
            org_id,
            "dogovor",
            prefix="Д",
            suffix="",
            start_from=48,
            template="{префикс}-{гггг}/{nnn}",
            reset_yearly=False,
            user_id=None,
        )
        db.commit()
        peek = peek_number(db, org_id, "dogovor", on=on)
        n, formatted = allocate_number(db, org_id, "dogovor", on=on)
        db.commit()
        assert n == 48
        assert peek == formatted == "Д-2026/048"
        peek2 = peek_number(db, org_id, "dogovor", suffix="/99", on=on)
        _, f2 = allocate_number(db, org_id, "dogovor", suffix="/99", on=on)
        db.commit()
        assert peek2 == f2 == "Д-2026/049"
    finally:
        db.close()


def test_yearly_reset(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "yearly@example.com")
    db = dbmod.SessionLocal()
    try:
        update_counter_numbering(
            db,
            org_id,
            "schet",
            prefix="С-",
            suffix="",
            start_from=10,
            template=DEFAULT_TEMPLATE,
            reset_yearly=True,
            user_id=None,
        )
        c = db.get(Counter, {"org_id": org_id, "key": "schet"})
        assert c is not None
        c.cycle_year = 2025
        db.commit()
        n, formatted = allocate_number(db, org_id, "schet", prefix="С-", on=date(2026, 1, 2))
        db.commit()
        assert n == 1
        assert formatted == "С-1"
        c = db.get(Counter, {"org_id": org_id, "key": "schet"})
        assert c.cycle_year == 2026
        assert c.value == 1
    finally:
        db.close()


def test_ban_rollback_below_used(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "rollback@example.com")
    db = dbmod.SessionLocal()
    try:
        update_counter_numbering(
            db,
            org_id,
            "dogovor",
            prefix="Д-",
            suffix="",
            start_from=50,
            template=DEFAULT_TEMPLATE,
            reset_yearly=False,
            user_id=None,
        )
        db.commit()
        try:
            update_counter_numbering(
                db,
                org_id,
                "dogovor",
                prefix="Д-",
                suffix="",
                start_from=40,
                template=DEFAULT_TEMPLATE,
                reset_yearly=False,
                user_id=None,
            )
            raise AssertionError("expected NumberingError")
        except NumberingError as exc:
            assert "максимум" in str(exc).lower() or "откатить" in str(exc).lower()
    finally:
        db.close()


def test_default_template_stored_as_null(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "null-tmpl@example.com")
    db = dbmod.SessionLocal()
    try:
        update_counter_numbering(
            db,
            org_id,
            "akt",
            prefix="А-",
            suffix="",
            start_from=1,
            template=DEFAULT_TEMPLATE,
            reset_yearly=False,
            user_id=None,
        )
        db.commit()
        c = db.get(Counter, {"org_id": org_id, "key": "akt"})
        assert c is not None
        assert c.number_template is None
    finally:
        db.close()


def test_onboarding_checklist_auto_and_dismiss(app):
    _, dbmod = app
    org_id, email = _seed_org(dbmod, "checklist@example.com", with_requisites=True)
    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        assert org is not None
        cl = build_onboarding_checklist(db, org, is_paid=False)
        assert cl.visible
        assert cl.steps[0].done is True
        assert cl.steps[1].done is False
        assert cl.can_dismiss is False

        confirm_numbering_defaults(db, org, None)
        db.commit()
        db.refresh(org)
        cl = build_onboarding_checklist(db, org, is_paid=False)
        assert cl.steps[1].done is True
        assert cl.can_dismiss is True

        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        db.add(
            Counterparty(
                org_id=org_id,
                type=CounterpartyType.ul,
                source=CounterpartySource.manual,
                name="ООО Контрагент",
                inn="7707083893",
            )
        )
        db.add(
            Document(
                org_id=org_id,
                template="dogovor.docx",
                file_path=f"{org_id}/demo.docx",
                format=DocumentFormat.docx,
                context={},
                created_by=user.id,
            )
        )
        db.commit()
        db.refresh(org)
        cl = build_onboarding_checklist(db, org, is_paid=False)
        assert cl.completed
        assert cl.visible is False

        org2 = Organization(name="W49B2", requisites=empty_requisites())
        db.add(org2)
        db.flush()
        confirm_numbering_defaults(db, org2, None)
        db.commit()
        cl2 = build_onboarding_checklist(db, org2, is_paid=False)
        assert cl2.can_dismiss
        dismiss_onboarding(org2)
        db.commit()
        db.refresh(org2)
        cl3 = build_onboarding_checklist(db, org2, is_paid=False)
        assert cl3.visible is False
    finally:
        db.close()


def test_numbering_page_and_dashboard(app):
    client, dbmod = app
    org_id, email = _seed_org(dbmod, "ui49@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303

    r = client.get("/cabinet/settings/numbering")
    assert r.status_code == 200
    assert "Шаблон" in r.text
    assert "dogovor" in r.text
    assert "{префикс}" in r.text
    assert "{n}" in r.text

    r = client.get("/cabinet/settings/counters", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/cabinet/settings/numbering"

    csrf = csrf_from(client, "/cabinet/settings/numbering")
    r = client.post(
        "/cabinet/settings/numbering",
        data={"csrf_token": csrf, "action": "confirm_defaults"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    home = client.get("/cabinet/")
    assert home.status_code == 200
    assert "Старт кабинета" in home.text
    assert "Нумерация" in home.text

    csrf = csrf_from(client, "/cabinet/")
    r = client.post(
        "/cabinet/onboarding/dismiss",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    home2 = client.get("/cabinet/")
    assert "Старт кабинета" not in home2.text

    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        assert org is not None
        assert org.onboarding and org.onboarding.get("dismissed") is True
    finally:
        db.close()


def test_admin_onboarding_kpi(app):
    client, dbmod = app
    org_id, _ = _seed_org(dbmod, "kpi49@example.com")
    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        assert org is not None
        org.created_at = datetime.now(timezone.utc) - timedelta(minutes=8)
        user = db.scalar(select(User).where(User.email == "kpi49@example.com"))
        assert user is not None
        db.add(
            Document(
                org_id=org_id,
                template="x.docx",
                file_path=f"{org_id}/x.docx",
                format=DocumentFormat.docx,
                context={},
                created_by=user.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "Онбординг" in r.text
    assert "Медиана" in r.text
