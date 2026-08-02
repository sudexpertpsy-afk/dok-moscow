"""P7 / ASVS V12.1: проверки загрузки .docx."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.docx_upload import DocxUploadError, validate_docx_bytes
from app.services.org_templates import OrgTemplateError, save_org_upload


def _docx_bytes(*, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        zf.writestr("word/document.xml", "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'/>")
        if extra:
            for name, data in extra.items():
                zf.writestr(name, data)
    return buf.getvalue()


def test_validate_accepts_minimal_docx():
    validate_docx_bytes(_docx_bytes())


def test_validate_rejects_empty_and_non_zip():
    with pytest.raises(DocxUploadError, match="Пустой"):
        validate_docx_bytes(b"")
    with pytest.raises(DocxUploadError, match="docx"):
        validate_docx_bytes(b"%PDF-1.4")


def test_validate_rejects_zip_without_word():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("readme.txt", "nope")
    with pytest.raises(DocxUploadError, match="word/document"):
        validate_docx_bytes(buf.getvalue())


def test_validate_rejects_too_many_entries():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w/>")
        for i in range(401):
            zf.writestr(f"pad/{i}.txt", "x")
    with pytest.raises(DocxUploadError, match="много файлов"):
        validate_docx_bytes(buf.getvalue())


def test_save_org_upload_strips_path_traversal(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services.org_templates import org_templates_dir

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    name = save_org_upload(
        org_id=1,
        filename="../evil.docx",
        data=_docx_bytes(),
    )
    assert name == "evil.docx"
    dest = org_templates_dir(1) / name
    assert dest.is_file()
    assert dest.resolve().parent == org_templates_dir(1).resolve()


def test_save_org_upload_rejects_non_docx(tmp_path, monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    with pytest.raises(OrgTemplateError, match="docx"):
        save_org_upload(org_id=1, filename="x.docx", data=b"not-a-zip")
