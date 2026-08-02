"""Админка: загрузка / переименование / удаление шаблонов DOCX."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from conftest import csrf_from, login


def _minimal_docx(text: str = "test") -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _templates_fixture(tmp_path, monkeypatch):
    root = tmp_path / "Шаблоны"
    root.mkdir()
    # базовый договор в реестре
    (root / "Договор_услуги_v2.docx").write_bytes(_minimal_docx())
    (root / "contracts_registry.json").write_text(
        '{"contracts":{"Физлицо":["Договор_услуги_v2.docx"],"Юрлицо":[],"Эксперт (ГПД)":[]},'
        '"self_contained":[],"package_templates":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TEMPLATES_DIR", str(root))
    from app.config import get_settings
    from app.services import templates as templates_svc

    get_settings.cache_clear()
    templates_svc.invalidate_templates_cache()
    return root


def test_admin_templates_page(app, tmp_path, monkeypatch):
    client, _ = app
    _templates_fixture(tmp_path, monkeypatch)
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/templates")
    assert r.status_code == 200
    assert "Шаблоны документов" in r.text
    assert "Договор услуги v2" in r.text
    assert 'name="file"' in r.text


def test_upload_rename_delete(app, tmp_path, monkeypatch):
    client, _ = app
    root = _templates_fixture(tmp_path, monkeypatch)
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/templates")

    r = client.post(
        "/admin/templates/upload",
        data={"csrf_token": token, "contract_type": "Юрлицо"},
        files={
            "file": (
                "Договор_тест_юрлицо.docx",
                _minimal_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (root / "Договор_тест_юрлицо.docx").is_file()

    from docfiller_core.contracts_registry import load_registry

    reg = load_registry(root)
    assert "Договор_тест_юрлицо.docx" in reg["contracts"]["Юрлицо"]

    token = csrf_from(client, "/admin/templates")
    r = client.post(
        "/admin/templates/rename",
        data={
            "csrf_token": token,
            "name": "Договор_тест_юрлицо.docx",
            "new_title": "Договор рецензия тест юрлицо",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert not (root / "Договор_тест_юрлицо.docx").exists()
    assert (root / "Договор_рецензия_тест_юрлицо.docx").is_file()
    reg = load_registry(root)
    assert "Договор_рецензия_тест_юрлицо.docx" in reg["contracts"]["Юрлицо"]

    token = csrf_from(client, "/admin/templates")
    r = client.post(
        "/admin/templates/delete",
        data={"csrf_token": token, "name": "Договор_рецензия_тест_юрлицо.docx"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert not (root / "Договор_рецензия_тест_юрлицо.docx").exists()
    reg = load_registry(root)
    assert "Договор_рецензия_тест_юрлицо.docx" not in reg["contracts"]["Юрлицо"]


def test_upload_rejects_non_docx(app, tmp_path, monkeypatch):
    client, _ = app
    _templates_fixture(tmp_path, monkeypatch)
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/templates")
    r = client.post(
        "/admin/templates/upload",
        data={"csrf_token": token, "contract_type": ""},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400
    assert "docx" in r.text.lower() or "Office" in r.text
