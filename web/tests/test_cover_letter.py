"""Сопроводительное письмо: каталог, форма, счётчик исх_номер, без договора."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from app.defaults import empty_requisites
from app.models import (
    Counter,
    Document,
    JobType,
    JobStatus,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.form_assist import field_meta
from app.services.jobs import enqueue_job
from app.services.templates import absolute_file, generate_docx, list_templates_for_org, templates_grouped
from conftest import csrf_from, login

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))
sys.path.insert(0, str(REPO / "core" / "tests"))
from fixtures_sample_context import build_context_for_template  # noqa: E402

TPL = "Сопроводительное_письмо.docx"


def _seed(dbmod, email="cover@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="CoverLetterOrg", requisites=empty_requisites())
        org.requisites["организация"]["полное_название"] = 'ООО «Тест Письмо»'
        org.requisites["организация"]["инн"] = "7707817216"
        org.requisites["исполнитель"] = {
            "должность": "Генеральный директор",
            "фио_кратко": "П.П. Подписантов",
        }
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
        return org.id, user.id
    finally:
        db.close()


def test_catalog_in_letters_group(app):
    client, dbmod = app
    org_id, _ = _seed(dbmod)
    assert login(client, "cover@example.com", "Passw0rd!").status_code == 303
    items = list_templates_for_org(org_id)
    groups = templates_grouped(items)
    group_names = [g for g, _ in groups]
    assert "Письма суду" in group_names
    letter_group = next(tpls for g, tpls in groups if g == "Письма суду")
    names = [t["name"] for t in letter_group]
    assert TPL in names
    r = client.get("/cabinet/documents/")
    assert r.status_code == 200
    assert "Сопроводительное" in r.text
    assert "Письма суду" in r.text


def test_form_defaults_and_textarea(app):
    client, dbmod = app
    _seed(dbmod, email="coverform@example.com")
    assert login(client, "coverform@example.com", "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/documents/new/{TPL}")
    assert r.status_code == 200
    assert "textarea" in r.text.lower()
    assert "Уважаемый суд!" in r.text or "уважаемый суд" in r.text.lower()
    # defaults «—» for вх
    assert "—" in r.text or "&mdash;" in r.text or "—" in r.text
    meta_n = field_meta("исх_номер", template_name=TPL)
    assert meta_n["is_number"] is True
    assert meta_n["counter_key"] == "ishod"
    meta_a = field_meta("адресат", template_name=TPL)
    assert meta_a["multiline"] is True
    assert meta_a["required"] is True
    meta_d = field_meta("исх_дата", template_name=TPL)
    assert meta_d["is_date"] is True
    assert meta_d["default"] == "сегодня"


def test_generate_ishod_counter_no_contract(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings
    from app.services.templates import template_variables

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id, _ = _seed(dbmod, email="covergen@example.com")
    assert login(client, "covergen@example.com", "Passw0rd!").status_code == 303

    csrf = csrf_from(client, f"/cabinet/documents/new/{TPL}")
    vars_ = template_variables(TPL, empty_requisites())
    ctx = build_context_for_template(TPL)
    data = {"csrf_token": csrf}
    for v in vars_:
        data[v] = str(ctx.get(v, ""))
    data["исх_номер"] = ""

    r = client.post(f"/cabinet/documents/new/{TPL}", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:800]
    loc = r.headers["location"]
    doc_id = int(loc.rstrip("/").split("/")[-1])

    db = dbmod.SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        assert doc is not None
        assert doc.template == TPL
        assert doc.contract_id is None
        assert doc.number == "1"
        assert doc.context.get("исх_номер") == "1"
        c = db.get(Counter, {"org_id": org_id, "key": "ishod"})
        assert c is not None
        assert c.value == 1
        path = Path(s.files_root) / doc.file_path
        assert path.is_file()
        xml = ZipFile(path).read("word/document.xml").decode("utf-8")
        assert "{{" not in xml
        assert "СОПРОВОДИТЕЛЬНОЕ" in xml
        assert "7707817216" in xml
    finally:
        db.close()

    jr = client.get("/cabinet/journal")
    assert jr.status_code == 200
    assert "Сопроводительное" in jr.text or TPL in jr.text


def test_pdf_conversion_cover_letter(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id, user_id = _seed(dbmod, email="coverpdf@example.com")
    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        ctx = build_context_for_template(TPL)
        doc = generate_docx(
            db=db,
            org=org,
            user_id=user_id,
            template_name=TPL,
            context=ctx,
            number=str(ctx.get("исх_номер") or "127-С/26"),
        )
        doc_id = doc.id

        def fake_convert(src: Path, dst: Path) -> Path:
            dst.write_bytes(b"%PDF-1.4 cover-letter")
            return dst

        with patch("app.services.jobs.convert_docx_to_pdf", side_effect=fake_convert):
            with patch("app.services.jobs.needs_watermark", return_value=False):
                job = enqueue_job(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    job_type=JobType.document_pdf,
                    payload={"document_id": doc_id},
                )
                assert job.status == JobStatus.succeeded
                assert job.result and job.result.get("document_id")
                assert not job.error
    finally:
        db.close()


def test_help_mentions_cover_letter(app):
    client, dbmod = app
    _seed(dbmod, email="coverhelp@example.com")
    assert login(client, "coverhelp@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/help")
    assert r.status_code == 200
    assert "Сопроводительное письмо" in r.text
    assert "Письма суду" in r.text
