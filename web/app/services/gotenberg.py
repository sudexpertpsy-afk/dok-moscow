"""Клиент Gotenberg: DOCX → PDF и сборка комплекта."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.config import get_settings


class GotenbergError(RuntimeError):
    pass


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> Path:
    """Конвертация через Gotenberg LibreOffice route."""
    url = get_settings().gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with docx_path.open("rb") as fh:
        files = {"files": (docx_path.name, fh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        try:
            response = httpx.post(url, files=files, timeout=120.0)
        except httpx.HTTPError as exc:
            raise GotenbergError(f"Gotenberg недоступен: {exc}") from exc
    if response.status_code >= 400:
        raise GotenbergError(f"Gotenberg HTTP {response.status_code}: {response.text[:200]}")
    pdf_path.write_bytes(response.content)
    return pdf_path


def merge_pdfs(paths: list[Path], output: Path) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for path in paths:
        writer.append(str(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        writer.write(fh)
    return output
