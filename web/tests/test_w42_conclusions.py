"""W-42: веб — форма заключения, счётчик, каталог, без пакета/договора."""

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
    DocumentFormat,
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

TPL = "Заключение_эксперта_гражданский_процесс.docx"


def _seed(dbmod, email="w42@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="W42Орг", requisites=empty_requisites())
        org.requisites["организация"]["полное_название"] = 'ООО «Тест Заключение»'
        org.requisites["организация"]["короткое_название"] = 'ООО «ТЗ»'
        org.requisites["организация"]["инн"] = "7707817216"
        org.requisites["организация"]["огрн"] = "5137746012619"
        org.requisites["организация"]["юр_адрес"] = "Москва"
        org.requisites["организация"]["телефон"] = "+7 495 000-00-00"
        org.requisites["организация"]["email"] = "w42@test.ru"
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


def test_catalog_has_conclusions_group(app):
    client, dbmod = app
    org_id, _ = _seed(dbmod, email="w42cat@example.com")
    assert login(client, "w42cat@example.com", "Passw0rd!").status_code == 303
    items = list_templates_for_org(org_id)
    names = {i["name"] for i in items}
    assert TPL in names
    assert "Заключение_эксперта_уголовный_процесс.docx" in names
    groups = templates_grouped(items)
    group_names = [g for g, _ in groups]
    assert "Заключения эксперта" in group_names
    assert "Письма суду" in group_names
    assert group_names.index("Письма суду") < group_names.index("Заключения эксперта")
    r = client.get("/cabinet/documents/")
    assert r.status_code == 200
    assert "Заключения эксперта" in r.text
    assert TPL in r.text
    assert 'class="doc-group-cards"' in r.text
    assert 'href="#doc-group-' in r.text
    assert 'id="doc-group-' in r.text
    assert "Заполнить" in r.text


def test_form_manifest_defaults_and_textarea(app):
    client, dbmod = app
    _seed(dbmod, email="w42form@example.com")
    assert login(client, "w42form@example.com", "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/documents/new/{TPL}")
    assert r.status_code == 200
    assert "textarea" in r.text
    assert "Вопросы" in r.text or "вопросы_эксперту" in r.text
    assert "не присутствовали" in r.text
    assert "не установлены" in r.text
    today = date.today().strftime("%d.%m.%Y")
    assert today in r.text
    meta = field_meta("вопросы_эксперту", template_name=TPL)
    assert meta["multiline"] is True
    meta_n = field_meta("номер_заключения", template_name=TPL)
    assert meta_n["is_number"] is True
    assert meta_n["counter_key"] == "zaklyuchenie_gpk"
    assert meta_n["counter_year_suffix"] is True


def test_generate_conclusion_counter_and_journal(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings
    from app.services.templates import template_variables

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id, _ = _seed(dbmod, email="w42gen@example.com")
    assert login(client, "w42gen@example.com", "Passw0rd!").status_code == 303

    csrf = csrf_from(client, f"/cabinet/documents/new/{TPL}")
    vars_ = template_variables(TPL, empty_requisites())
    ctx = build_context_for_template(TPL)
    data = {"csrf_token": csrf}
    for v in vars_:
        data[v] = str(ctx.get(v, ""))
    data["номер_заключения"] = ""

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
        yy = date.today().strftime("%y")
        assert doc.number == f"1/{yy}"
        assert doc.context.get("номер_заключения") == f"1/{yy}"
        c = db.get(Counter, {"org_id": org_id, "key": "zaklyuchenie_gpk"})
        assert c is not None
        assert c.value == 1
        assert c.suffix == f"/{yy}"
        path = Path(s.files_root) / doc.file_path
        assert path.is_file()
        xml = ZipFile(path).read("word/document.xml").decode("utf-8")
        assert "{{" not in xml
        assert "7707817216" in xml
    finally:
        db.close()

    jr = client.get("/cabinet/journal")
    assert jr.status_code == 200
    assert "заключение" in jr.text.lower()
    assert TPL in jr.text


def test_pdf_conversion_for_conclusion(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id, user_id = _seed(dbmod, email="w42pdf@example.com")
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
            number=str(ctx.get("номер_заключения") or "1/26"),
        )
        doc_id = doc.id

        def fake_convert(src: Path, dst: Path) -> Path:
            dst.write_bytes(b"%PDF-1.4 w42-conclusion")
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
                # PDF-конвертация прошла без ошибки Gotenberg
                assert not job.error
    finally:
        db.close()
