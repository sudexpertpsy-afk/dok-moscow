"""Кабинет: свои шаблоны организации (тариф «Организация»)."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Organization,
    Subscription,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from app.services.org_templates import (
    can_manage_org_templates,
    org_contract_names,
    org_templates_dir,
    save_org_upload,
)
from app.services.package_master import contract_options
from app.services.templates import list_templates_for_org, resolve_template_path
from conftest import csrf_from, login


def _minimal_docx(text: str = "test") -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _org_user(dbmod, email: str = "orgtpl@example.com"):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="ООО СвоиШаблоны", requisites=empty_requisites())
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
        db.commit()
        return org.id, email
    finally:
        db.close()


def _set_tariff(dbmod, org_id: int, code: TariffCode) -> None:
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        tariff = db.scalar(select(Tariff).where(Tariff.code == code))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        sub.tariff_id = tariff.id
        sub.status = SubscriptionStatus.active
        sub.ends_at = utcnow() + timedelta(days=30)
        sub.is_beta = False
        db.commit()
    finally:
        db.close()


def test_resolve_prefers_org_over_shared(app, tmp_path, monkeypatch):
    client, dbmod = app
    org_id, _ = _org_user(dbmod)
    shared = tmp_path / "Шаблоны"
    shared.mkdir()
    (shared / "Мой_договор.docx").write_bytes(_minimal_docx("shared"))
    monkeypatch.setenv("TEMPLATES_DIR", str(shared))
    from app.config import get_settings
    from app.services import templates as templates_svc

    get_settings.cache_clear()
    templates_svc.invalidate_templates_cache()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_dir = org_templates_dir(org_id)
    (org_dir / "Мой_договор.docx").write_bytes(_minimal_docx("org"))

    path = resolve_template_path("Мой_договор.docx", org_id)
    assert path.parent == org_dir
    assert path.read_bytes() == _minimal_docx("org")


def test_list_templates_for_org_marks_source(app, tmp_path, monkeypatch):
    _, dbmod = app
    org_id, _ = _org_user(dbmod)
    shared = tmp_path / "Шаблоны"
    shared.mkdir()
    (shared / "Общий.docx").write_bytes(_minimal_docx())
    monkeypatch.setenv("TEMPLATES_DIR", str(shared))
    from app.config import get_settings
    from app.services import templates as templates_svc

    get_settings.cache_clear()
    templates_svc.invalidate_templates_cache()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    save_org_upload(
        org_id=org_id,
        filename="Свой_шаблон.docx",
        data=_minimal_docx(),
        contract_type="Физлицо",
    )
    items = list_templates_for_org(org_id)
    by_name = {i["name"]: i for i in items}
    assert by_name["Свой_шаблон.docx"]["source"] == "org"
    assert by_name["Общий.docx"]["source"] == "shared"
    assert "Свой_шаблон.docx" in org_contract_names(org_id, "Физлицо")
    opts = contract_options("Физлицо", org_id)
    assert "Свой_шаблон.docx" in opts


def test_cabinet_templates_page_and_upload(app, tmp_path, monkeypatch):
    client, dbmod = app
    org_id, email = _org_user(dbmod)
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    assert login(client, email, "Passw0rd!").status_code == 303
    r = client.get("/cabinet/templates/")
    assert r.status_code == 200
    assert "Мои шаблоны" in r.text
    assert 'name="file"' in r.text
    assert 'href="/cabinet/templates/"' in r.text or "Мои шаблоны" in r.text

    token = csrf_from(client, "/cabinet/templates/")
    r = client.post(
        "/cabinet/templates/upload",
        data={"csrf_token": token, "contract_type": "Юрлицо"},
        files={
            "file": (
                "Договор_орг_юр.docx",
                _minimal_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "/cabinet/templates/upload/confirm" in loc
    conf = client.get(loc)
    assert conf.status_code == 200
    assert "Подтверждение" in conf.text
    import re as _re

    m = _re.search(r'name="csrf_token" value="([^"]+)"', conf.text)
    assert m
    m2 = _re.search(r'name="token" value="([^"]+)"', conf.text)
    assert m2
    r = client.post(
        "/cabinet/templates/upload/confirm",
        data={
            "csrf_token": m.group(1),
            "token": m2.group(1),
            "contract_type": "Юрлицо",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/cabinet/templates/" in r.headers["location"]
    dest = Path(s.files_root) / str(org_id) / "templates" / "Договор_орг_юр.docx"
    assert dest.is_file()

    r = client.get("/cabinet/documents/")
    assert r.status_code == 200
    assert "Договор орг юр" in r.text or "Договор_орг_юр" in r.text
    assert "свой" in r.text

    r = client.get("/cabinet/package/step2")
    # мастер без шага 1 может редиректить — главное что опции содержат org-договор при вызове сервиса
    assert "Договор_орг_юр.docx" in contract_options("Юрлицо", org_id)


def test_specialist_cannot_manage_org_templates(app, tmp_path, monkeypatch):
    client, dbmod = app
    org_id, email = _org_user(dbmod, email="spec-tpl@example.com")
    _set_tariff(dbmod, org_id, TariffCode.specialist)
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    db = dbmod.SessionLocal()
    try:
        ok, reason = can_manage_org_templates(db, org_id)
        assert ok is False
        assert reason and "Организация" in reason
    finally:
        db.close()

    assert login(client, email, "Passw0rd!").status_code == 303
    r = client.get("/cabinet/templates/")
    assert r.status_code == 200
    assert "Организация" in r.text
    assert 'name="file"' not in r.text

    token = csrf_from(client, "/cabinet/billing/")
    r = client.post(
        "/cabinet/templates/upload",
        data={"csrf_token": token, "contract_type": ""},
        files={
            "file": (
                "x.docx",
                _minimal_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/cabinet/billing/" in r.headers["location"]
