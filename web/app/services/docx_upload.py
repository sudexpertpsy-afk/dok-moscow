"""Проверка загружаемых .docx (ASVS V12.1 / P7)."""

from __future__ import annotations

import io
import zipfile

from fastapi import UploadFile

MAX_DOCX_BYTES = 25 * 1024 * 1024
MAX_ZIP_ENTRIES = 400
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_ORG_TEMPLATES = 50


class DocxUploadError(Exception):
    """Файл не прошёл проверку загрузки."""


def validate_docx_bytes(data: bytes) -> None:
    if not data:
        raise DocxUploadError("Пустой файл")
    if len(data) > MAX_DOCX_BYTES:
        raise DocxUploadError("Файл больше 25 МБ")
    if data[:2] != b"PK":
        raise DocxUploadError("Нужен файл .docx (Office Open XML)")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise DocxUploadError("Нужен файл .docx (повреждённый ZIP)") from exc

    if len(names) > MAX_ZIP_ENTRIES:
        raise DocxUploadError("Слишком много файлов внутри архива")
    if "[Content_Types].xml" not in names:
        raise DocxUploadError("Некорректный .docx: нет [Content_Types].xml")
    if "word/document.xml" not in names:
        raise DocxUploadError("Некорректный .docx: нет word/document.xml")

    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.template_security import zip_has_vba_or_encryption

    blocked = zip_has_vba_or_encryption(data)
    if blocked:
        raise DocxUploadError(blocked)

    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.file_size < 0 or info.compress_size < 0:
                    raise DocxUploadError("Повреждённый архив")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                    raise DocxUploadError("Слишком большой распакованный объём")
                if (
                    info.compress_size > 0
                    and info.file_size > 1_000_000
                    and info.file_size / info.compress_size > 100
                ):
                    raise DocxUploadError("Подозрительное сжатие архива")
    except zipfile.BadZipFile as exc:
        raise DocxUploadError("Нужен файл .docx (повреждённый ZIP)") from exc


async def read_upload_limited(
    file: UploadFile,
    *,
    max_bytes: int = MAX_DOCX_BYTES,
) -> bytes:
    """Читать UploadFile чанками; не буферизовать сверх лимита."""
    cl = file.headers.get("content-length") if file.headers else None
    if cl is not None:
        try:
            if int(cl) > max_bytes:
                raise DocxUploadError("Файл больше 25 МБ")
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DocxUploadError("Файл больше 25 МБ")
        chunks.append(chunk)
    return b"".join(chunks)
