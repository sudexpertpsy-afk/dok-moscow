#!/usr/bin/env python3
"""Сгенерировать образцы PDF для лендинга из настоящих шаблонов Шаблонера.

Использование (из корня репозитория):

    python scripts/make_samples.py
    GOTENBERG_URL=http://127.0.0.1:3000 python scripts/make_samples.py

Не сочинять PDF вручную: только fill_template(docfiller_core) → Gotenberg →
водяной знак «ОБРАЗЕЦ» + колонтитул. Результат: web/app/static/samples/.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "core"
TEMPLATES = CORE / "Шаблоны"
OUT_DIR = REPO / "web" / "app" / "static" / "samples"
IMG_DIR = REPO / "web" / "app" / "static" / "img"

# PDF лендинга → WebP-миниатюра первой страницы (W-37 / W-44)
THUMBS: tuple[tuple[str, str], ...] = (
    ("dogovor-fl.pdf", "sample-dogovor.webp"),
    ("schet.pdf", "sample-schet.webp"),
    ("akt.pdf", "sample-akt.webp"),
    ("zaklyuchenie-fragment.pdf", "sample-zaklyuchenie.webp"),
    ("schet-faksimile.pdf", "sample-schet-faksimile.webp"),
)

sys.path.insert(0, str(CORE))
from docfiller_core.filler import fill_template, list_template_variables  # noqa: E402

# Соответствие URL лендинга → шаблон
# max_pages: обрезать PDF после водяного знака (фрагмент заключения)
SAMPLES: tuple[tuple[str, str, int | None], ...] = (
    ("dogovor-fl.pdf", "Договор_услуги_v2.docx", None),
    ("schet.pdf", "Счёт_на_оплату.docx", None),
    ("akt.pdf", "Акт_оказанных_услуг.docx", None),
    ("zaklyuchenie-fragment.pdf", "Заключение_эксперта_гражданский_процесс.docx", 2),
    ("schet-faksimile.pdf", "Счёт_на_оплату.docx", None),
)

# Реквизиты исполнителя — публичные с /requisites (АО «ТБанк»).
# Клиентские данные — вымышленные (витрина).
DEMO_SETTINGS: dict = {
    "организация": {
        "короткое_название": "ООО «УСЭ»",
        "полное_название": "ООО «УЧРЕЖДЕНИЕ СУДЕБНОЙ ЭКСПЕРТИЗЫ»",
        "инн": "7707817216",
        "кпп": "771401001",
        "огрн": "5137746012619",
        "юр_адрес": (
            "125124, г. Москва, 3-я улица Ямского Поля, д. 2, стр. 13, оф. 221"
        ),
        "почтовый_адрес": (
            "125124, г. Москва, 3-я улица Ямского Поля, д. 2, стр. 13, оф. 221"
        ),
        "телефон": "8 (495) 414-20-63",
        "email": "post@use.moscow",
        "лицензия": (
            "Лицензия на осуществление медицинской деятельности "
            "ЛО-77-01-020999 от 12.08.2020"
        ),
        "окпо": "18920830",
        "город": "г. Москва",
    },
    "исполнитель": {
        "фио": "Лосев Андрей Васильевич",
        "фио_кратко": "Лосев А.В.",
        "должность": "Генеральный директор",
        "должность_род": "Генерального директора",
        "основание": "Устава",
    },
    "бухгалтер": {"фио": "Лосев Андрей Васильевич"},
    "кассир": {"фио": "Лосев Андрей Васильевич"},
    "банк": {
        "расчётный_счёт": "40702810610000259195",
        "банк": "АО «ТБанк»",
        "бик": "044525974",
        "корр_счёт": "30101810145250000974",
    },
    "прайс": {
        "сппэ": 54000,
        "кспэ": 50000,
        "рецензия": 30000,
        "обучение_спэ": 30000,
        "обучение_полиграф": 30000,
    },
    "константы": {
        "срок_дней_по_умолчанию": 30,
        "срок_оплаты_счёта_дней": 5,
        "срок_оплаты_гпд_дней": 3,
    },
}

DEMO_CONTEXT: dict = {
    "номер_договора": "Д-2026/042",
    "номер_счёта": "СЧ-2026/042",
    "номер_акта": "АКТ-2026/042",
    "номер_заключения": "42/26",
    "номер_дела": "2-4567/2026",
    "фио_клиента": "Иванова Мария Сергеевна",
    "плательщик": "Иванова Мария Сергеевна",
    "email_клиента": "ivanova.ms@example.ru",
    "телефон_клиента": "+7 (916) 123-45-67",
    "адрес_клиента": "г. Москва, ул. Образцовая, д. 10, кв. 5",
    "дата_договора": "15.07.2026",
    "дата_счёта": "15.07.2026",
    "дата_акта": "28.07.2026",
    "дата_начала": "16.07.2026",
    "дата_окончания": "28.07.2026",
    "дата_заключения": "28.07.2026",
    "дата_основания": "01.07.2026",
    "сумма": 54000,
    "сумма_к_оплате": 54000,
    "стоимость": 54000,
    "цена_один": 54000,
    "цена_итог": 54000,
    "предмет_договора": "Проведение судебно-психологической экспертизы",
    "предмет_услуг": "Составление экспертного заключения",
    "наименование_услуги": "Судебно-психологическая экспертиза (СППЭ)",
    "итоговый_документ": "Заключение эксперта",
    "срок_дней": 30,
    "срок_рабочих_дней": 10,
    "условие_срока": "календарных",
    "сколько_заключений": 1,
    "тип_экспертизы": "СППЭ",
    "дополнительные_условия": "Расчёты произведены полностью.",
    "документ_основания": "Паспорт гражданина РФ",
    "реквизиты_документа": "серия 4500 № 123456, выдан 01.01.2015",
    # заключение (вымышленные данные витрины)
    "фио_эксперта": "Образцов Образец Образцович",
    "фио_эксперта_дат": "Образцову Образцу Образцовичу",
    "фио_эксперта_кратко": "О.О. Образцов",
    "вид_экспертизы": "судебно-психологическая экспертиза",
    "вид_экспертизы_род": "судебно-психологической экспертизы",
    "время_начала": "10:00",
    "время_окончания": "17:30",
    "место_производства": "г. Москва, ул. Образцовая, д. 1",
    "назначивший": "Тверской районный суд г. Москвы",
    "образование_эксперта": "высшее психологическое",
    "специальность_эксперта": "психология",
    "стаж_эксперта": "12 лет",
    "учёная_степень": "не имеет",
    "должность_эксперта": "судебный эксперт",
    "вопросы_эксперту": (
        "1. Имеются ли у подэкспертного признаки?\n"
        "2. Способен ли он осознавать значение своих действий?"
    ),
    "объекты_исследования": "Материалы гражданского дела, медицинская документация",
    "материалы_дела": "Том 1, л.д. 1–80",
    "присутствовавшие": "не присутствовали",
    "применённые_методы": "Клиническая беседа, анализ документов",
    "содержание_исследования": (
        "Проведено исследование по стандартной методике (образец витрины)."
    ),
    "оценка_результатов": "Результаты согласуются между собой (образец).",
    "выводы": (
        "1. Признаков не выявлено.\n"
        "2. Способен осознавать значение своих действий."
    ),
    "дополнительные_обстоятельства": "не установлены",
    "приложения": "Приложение № 1 — схема исследования",
    "год": "2026",
}


def _liberation_font() -> str:
    candidates = [
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            name = "SampleCyr"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    raise SystemExit(
        "Не найдены шрифты Liberation/DejaVu. Установите fonts-liberation "
        "(apt install fonts-liberation) — нужны русские метрики для водяного знака."
    )


def build_context(template_name: str) -> dict:
    variables = list_template_variables(TEMPLATES / template_name)
    ctx = {}
    for var in variables:
        if var in DEMO_CONTEXT:
            ctx[var] = DEMO_CONTEXT[var]
        else:
            ctx[var] = f"—"
    return ctx


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path, gotenberg_url: str) -> None:
    url = gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
    with docx_path.open("rb") as fh:
        files = {
            "files": (
                docx_path.name,
                fh,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }
        try:
            response = httpx.post(url, files=files, timeout=120.0)
        except httpx.HTTPError as exc:
            raise SystemExit(f"Gotenberg недоступен ({gotenberg_url}): {exc}") from exc
    if response.status_code >= 400:
        raise SystemExit(
            f"Gotenberg HTTP {response.status_code}: {response.text[:300]}"
        )
    pdf_path.write_bytes(response.content)


def _watermark_overlay(width: float, height: float, font_name: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.saveState()
    c.setFillColor(Color(0.55, 0.55, 0.55, alpha=0.18))
    c.setFont(font_name, 54)
    c.translate(width * 0.5, height * 0.5)
    c.rotate(40)
    c.drawCentredString(0, 0, "ОБРАЗЕЦ")
    c.restoreState()
    c.setFillColor(Color(0.25, 0.25, 0.25, alpha=0.75))
    c.setFont(font_name, 8)
    footer = "Обезличенный образец. Документ сформирован сервисом Док.Москва"
    c.drawCentredString(width / 2, 18, footer)
    c.save()
    return buf.getvalue()


def apply_sample_watermark(pdf_path: Path, *, max_pages: int | None = None) -> None:
    font_name = _liberation_font()
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        raise SystemExit(f"Пустой PDF: {pdf_path}")
    writer = PdfWriter()
    pages = list(reader.pages)
    if max_pages is not None:
        pages = pages[: max(1, int(max_pages))]
    for page in pages:
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        wm = PdfReader(io.BytesIO(_watermark_overlay(w, h, font_name))).pages[0]
        page.merge_page(wm)
        writer.add_page(page)
    tmp = pdf_path.with_suffix(".wm.pdf")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(pdf_path)


def _demo_stamp_png(path: Path) -> None:
    """Демо-печать «ОБРАЗЕЦ» (не реальная)."""
    from PIL import Image, ImageDraw, ImageFont

    size = 600
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([20, 20, size - 20, size - 20], outline=(160, 40, 40, 220), width=10)
    d.ellipse([50, 50, size - 50, size - 50], outline=(160, 40, 40, 180), width=3)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48
        )
    except OSError:
        font = ImageFont.load_default()
    text = "ОБРАЗЕЦ"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - tw) / 2, (size - th) / 2 - 10), text, fill=(160, 40, 40, 220), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")


def _demo_sign_png(path: Path) -> None:
    """Демо-подпись (волнистая линия, не реальная)."""
    from PIL import Image, ImageDraw

    w, h = 770, 260
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pts = []
    for x in range(40, w - 40, 4):
        import math

        y = h // 2 + int(28 * math.sin(x / 28.0)) + int(12 * math.sin(x / 11.0))
        pts.append((x, y))
    d.line(pts, fill=(20, 40, 120, 230), width=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")


def render_thumbs(samples_dir: Path, img_dir: Path) -> None:
    """Первая страница PDF → WebP ~480px по ширине (для карточек лендинга)."""
    import shutil
    import subprocess

    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "Нужен Pillow: pip install pillow. Для pdftoppm — poppler-utils."
        ) from exc

    if not shutil.which("pdftoppm"):
        raise SystemExit("Нет pdftoppm (apt install poppler-utils)")

    img_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dok-thumbs-") as tmp:
        tmp_path = Path(tmp)
        for pdf_name, webp_name in THUMBS:
            pdf_path = samples_dir / pdf_name
            if not pdf_path.is_file():
                raise SystemExit(f"Нет PDF для миниатюры: {pdf_path}")
            prefix = tmp_path / pdf_path.stem
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    "120",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    str(pdf_path),
                    str(prefix),
                ],
                check=True,
            )
            png = prefix.parent / f"{prefix.name}-1.png"
            if not png.is_file():
                raise SystemExit(f"pdftoppm не создал {png}")
            im = Image.open(png).convert("RGB")
            im.thumbnail((480, 960), Image.Resampling.LANCZOS)
            out = img_dir / webp_name
            im.save(out, "WEBP", quality=82, method=4)
            print(f"  thumb {out} ({im.size[0]}×{im.size[1]})")


def generate(gotenberg_url: str, out_dir: Path, thumbs: bool = True) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dok-samples-") as tmp:
        tmp_path = Path(tmp)
        stamp = tmp_path / "demo_stamp.png"
        sign = tmp_path / "demo_sign.png"
        _demo_stamp_png(stamp)
        _demo_sign_png(sign)
        for pdf_name, template_name, max_pages in SAMPLES:
            src = TEMPLATES / template_name
            if not src.is_file():
                raise SystemExit(f"Нет шаблона: {src}")
            docx_out = tmp_path / f"{pdf_name}.docx"
            ctx = build_context(template_name)
            images = None
            if pdf_name == "schet-faksimile.pdf":
                images = {
                    "факсимиле_печать": stamp,
                    "факсимиле_директор": sign,
                }
            print(f"→ {template_name} ({len(ctx)} полей) → {pdf_name}")
            fill_template(src, docx_out, ctx, settings=DEMO_SETTINGS, images=images)
            pdf_out = out_dir / pdf_name
            convert_docx_to_pdf(docx_out, pdf_out, gotenberg_url)
            apply_sample_watermark(pdf_out, max_pages=max_pages)
            pages = len(PdfReader(str(pdf_out)).pages)
            print(f"  OK {pdf_out} ({pages} стр., {pdf_out.stat().st_size} байт)")
    if thumbs:
        print("→ миниатюры WebP")
        render_thumbs(out_dir, IMG_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gotenberg-url",
        default=os.environ.get("GOTENBERG_URL", "http://127.0.0.1:3000"),
        help="URL Gotenberg (по умолчанию GOTENBERG_URL или http://127.0.0.1:3000)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help=f"Каталог образцов (по умолчанию {OUT_DIR})",
    )
    parser.add_argument(
        "--thumbs-only",
        action="store_true",
        help="Только пересобрать WebP-миниатюры из уже готовых PDF",
    )
    parser.add_argument(
        "--no-thumbs",
        action="store_true",
        help="Не генерировать WebP-миниатюры после PDF",
    )
    args = parser.parse_args()
    if not TEMPLATES.is_dir():
        raise SystemExit(f"Нет каталога шаблонов: {TEMPLATES}")
    if args.thumbs_only:
        render_thumbs(args.out_dir, IMG_DIR)
    else:
        generate(args.gotenberg_url, args.out_dir, thumbs=not args.no_thumbs)
    print("Готово. Образцы готовы к выкладке на лендинг (#samples).")


if __name__ == "__main__":
    main()
