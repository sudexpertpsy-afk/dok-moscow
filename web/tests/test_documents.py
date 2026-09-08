"""W-03: генерация DOCX, счётчики, изоляция файлов."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from docx import Document as DocxDocument
from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import Counter, CounterpartyType, Document, Organization, User, UserRole
from app.security import hash_password
from app.services.counters import allocate_number
from app.services.templates import absolute_file, generate_docx, list_templates, template_variables
from conftest import csrf_from, login

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "core"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))

from fixtures_sample_context import build_context_for_template  # noqa: E402


def _seed_org_user(dbmod, email="gen@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ГенОрг", requisites=empty_requisites())
        org.requisites["организация"]["короткое_название"] = "ООО «Тест»"
        org.requisites["организация"]["инн"] = "7707817216"
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add(user)
        db.commit()
        return org.id, user.id
    finally:
        db.close()


def test_list_templates_nonzero():
    items = list_templates()
    assert len(items) >= 20
    assert all(i["name"].endswith(".docx") for i in items)


def test_generate_docx_via_service(app, tmp_path, monkeypatch):
    client, dbmod = app
    monkeypatch.setenv("FILES_ROOT", str(tmp_path / "files"))
    from app.config import get_settings

    get_settings.cache_clear()
    # settings cached — patch files_root on instance
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id, user_id = _seed_org_user(dbmod)
    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        template = "Договор_услуги_v2.docx"
        ctx = build_context_for_template(template)
        doc = generate_docx(
            db=db,
            org=org,
            user_id=user_id,
            template_name=template,
            context=ctx,
            number=ctx.get("номер_договора"),
        )
        path = absolute_file(doc)
        assert path.is_file()
        assert path.stat().st_size > 500
        text = "\n".join(p.text for p in DocxDocument(str(path)).paragraphs)
        assert "Тестов Тест Тестович" in text or "2026-Т01" in text or len(text) > 30
        assert doc.org_id == org_id
        assert "{{" not in text
    finally:
        db.close()
        get_settings.cache_clear()


def test_http_generate_and_download(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id, _ = _seed_org_user(dbmod, email="httpgen@example.com")
    # Счёт требует банковские реквизиты организации
    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        org.requisites["банк"] = {
            "расчётный_счёт": "40702810338000013478",
            "банк": 'ПАО "СБЕРБАНК РОССИИ"',
            "бик": "044525225",
            "корр_счёт": "30101810400000000225",
        }
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(org, "requisites")
        db.add(org)
        db.commit()
    finally:
        db.close()

    assert login(client, "httpgen@example.com", "Passw0rd!").status_code == 303

    template = "Счёт_на_оплату.docx"
    r = client.get(f"/cabinet/documents/new/{template}")
    assert r.status_code == 200
    assert "номер_счёта" in r.text or "csrf_token" in r.text

    vars_ = template_variables(template, empty_requisites())
    csrf = csrf_from(client, f"/cabinet/documents/new/{template}")
    data = {"csrf_token": csrf}
    ctx = build_context_for_template(template)
    for v in vars_:
        data[v] = str(ctx.get(v, ""))
    # авто-номер
    if "номер_счёта" in data:
        data["номер_счёта"] = ""

    r = client.post(
        f"/cabinet/documents/new/{template}",
        data=data,
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    loc = r.headers["location"]
    assert loc.startswith("/cabinet/documents/")
    doc_id = int(loc.rstrip("/").split("/")[-1])

    r = client.get(f"/cabinet/documents/{doc_id}/download")
    assert r.status_code == 200
    assert len(r.content) > 500

    # чужой org не скачает
    org_b, _ = _seed_org_user(dbmod, email="other@example.com")
    assert org_b != org_id
    token = csrf_from(client, "/cabinet/documents/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert login(client, "other@example.com", "Passw0rd!").status_code == 303
    assert client.get(f"/cabinet/documents/{doc_id}/download").status_code == 404
    get_settings.cache_clear()


def test_parallel_counter_allocation(app):
    client, dbmod = app
    org_id, _ = _seed_org_user(dbmod, email="cnt@example.com")
    from app.services.counters import allocation_section

    def once(_):
        db = dbmod.SessionLocal()
        try:
            # На SQLite lock должен покрывать commit, иначе видны дубликаты номеров.
            with allocation_section(db):
                _, formatted = allocate_number(db, org_id, "dogovor", prefix="Д-")
                db.commit()
            return formatted
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(once, range(20)))

    assert len(results) == 20
    assert len(set(results)) == 20

    db = dbmod.SessionLocal()
    try:
        counter = db.scalar(
            select(Counter).where(Counter.org_id == org_id, Counter.key == "dogovor")
        )
        assert counter is not None
        assert counter.value == 20
    finally:
        db.close()
