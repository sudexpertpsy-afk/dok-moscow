"""Водяной знак на PDF для тарифа «Гость» (W-12)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def apply_guest_watermark(pdf_path: Path) -> Path:
    """Наложить водяной знак на все страницы PDF (in-place)."""
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        return pdf_path
    box = reader.pages[0].mediabox
    width = float(box.width)
    height = float(box.height)
    wm_reader = PdfReader(BytesIO(_minimal_watermark_pdf(width, height)))
    wm_page = wm_reader.pages[0]

    writer = PdfWriter()
    for page in reader.pages:
        page.merge_page(wm_page)
        writer.add_page(page)

    tmp = pdf_path.with_suffix(".wm.pdf")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(pdf_path)
    return pdf_path


def _minimal_watermark_pdf(width: float, height: float) -> bytes:
    """Минимальный PDF 1.4 с текстом водяного знака."""
    w, h = width, height
    label = "DOK.MOSCOW / GUEST"
    content_lines = [
        "BT",
        "/F1 26 Tf",
        "0.82 g",
        "0.7071 0.7071 -0.7071 0.7071 72 180 Tm",
        f"({label}) Tj",
        "0.7071 0.7071 -0.7071 0.7071 120 360 Tm",
        f"({label}) Tj",
        "ET",
    ]
    stream = "\n".join(content_lines).encode("ascii")
    objects: list[bytes] = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            f"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:.2f} {h:.2f}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
        ).encode("ascii"),
        (
            f"4 0 obj<< /Length {len(stream)} >>stream\n".encode("ascii")
            + stream
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)
