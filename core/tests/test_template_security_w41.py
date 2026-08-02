"""W-41: безопасность пользовательских DOCX (фильтры, {% %}, SSTI)."""

from __future__ import annotations

import io

import pytest
from docx import Document

from docfiller_core.template_security import (
    TemplateSecurityError,
    analyze_docx_template,
    assert_safe_docx_template,
    zip_has_vba_or_encryption,
)


def _docx(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extracts_variables_and_allowed_filter():
    a = analyze_docx_template(_docx("{{ сумма | руб }} и {{ дата_договора | дата_рус }}"))
    assert "сумма" in a.variables
    assert "дата_договора" in a.variables
    assert "руб" in a.filters_used
    assert "дата_рус" in a.filters_used


@pytest.mark.parametrize(
    "text",
    [
        "{% for x in items %}{{ x }}{% endfor %}",
        "{% if x %}{{ x }}{% endif %}",
        "{% set x = 1 %}{{ x }}",
        "{% macro m() %}x{% endmacro %}",
    ],
)
def test_rejects_statements(text):
    with pytest.raises(TemplateSecurityError, match=r"\{\%"):
        assert_safe_docx_template(_docx(text))


def test_rejects_unknown_filter():
    with pytest.raises(TemplateSecurityError, match="фильтр"):
        assert_safe_docx_template(_docx("{{ x | totally_unknown_filter }}"))


@pytest.mark.parametrize(
    "text",
    [
        "{{ cycler.__init__.__globals__ }}",
        "{{ ''.__class__.__mro__ }}",
        "{{ [].__class__.__base__ }}",
        "{{ lipsum() }}",
        "{{ cycler }}",
    ],
)
def test_rejects_ssti_patterns(text):
    with pytest.raises(TemplateSecurityError):
        assert_safe_docx_template(_docx(text))


def test_vba_and_encryption_detection():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w/>")
        zf.writestr("word/vbaProject.bin", b"x")
    assert zip_has_vba_or_encryption(buf.getvalue())

    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as zf:
        zf.writestr("EncryptedPackage", b"x")
        zf.writestr("EncryptionInfo", b"x")
    assert zip_has_vba_or_encryption(buf2.getvalue())
