#!/usr/bin/env python3
"""W-43: inject {{ факсимиле_директор }} / {{ факсимиле_печать }} into built-in DOCX templates.

Conditional Jinja `{% if ... %}` blocks are NOT inserted: template_security.py
rejects `{% %}` statements (SandboxedEnvironment + statement walk).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "core" / "Шаблоны"

PH_DIR = "{{ факсимиле_директор }}"
PH_STAMP = "{{ факсимиле_печать }}"

ALLOWED: frozenset[str] = frozenset(
    {
        "Счёт_на_оплату.docx",
        "Счёт_на_оплату_юрлицо.docx",
        "Сопроводительное_письмо.docx",
        "СППЭ_информация_суду.docx",
        "Психология_ДРО_с_итогом.docx",
    }
)

WARN: frozenset[str] = frozenset(
    {
        "Договор_услуги_v2.docx",
        "Договор_услуги_юрлицо.docx",
        "Договор_услуги_с_печатью.docx",
        "Договор_рецензия.docx",
        "Договор_рецензия_юрлицо.docx",
        "Договор_обучение_СПЭ.docx",
        "Договор_обучение_СПЭ_юрлицо.docx",
        "Договор_обучение_полиграф.docx",
        "Договор_освидетельствование.docx",
        "Договор_ГПД_эксперт.docx",
        "Акт_оказанных_услуг.docx",
        "Акт_оказанных_услуг_юрлицо.docx",
        "Акт_ГПД_эксперт.docx",
        "Допсоглашение_продление.docx",
        "Допсоглашение_продление_юрлицо.docx",
        "Соглашение_расторжение.docx",
        "Соглашение_расторжение_юрлицо.docx",
        "Уведомление_расторжение.docx",
        "Уведомление_расторжение_юрлицо.docx",
    }
)

FORBIDDEN: frozenset[str] = frozenset(
    {
        "ПКО_КО-1.docx",
        "Заключение_эксперта_гражданский_процесс.docx",
        "Заключение_эксперта_уголовный_процесс.docx",
        "Согласие_ПДн.docx",
        "Ходатайство_о_назначении_экспертизы.docx",
    }
)

# {% %} forbidden by core/docfiller_core/template_security.py
SKIP_CONDITIONAL_BLOCKS = True

_UNDERSCORES = re.compile(r"_{3,}")
_COUNTERPARTY = re.compile(
    r"Заказчик|Покупатель|фио_клиента|фио_подписанта",
    re.IGNORECASE,
)
_OUR_SIDE = re.compile(
    r"Исполнител|исполнитель\.|Руководитель|Лосев|должность",
    re.IGNORECASE,
)


def _is_our_signature_line(text: str) -> bool:
    """Director facsimile belongs on our org signature lines, not counterparty."""
    has_counter = bool(_COUNTERPARTY.search(text))
    has_ours = bool(_OUR_SIDE.search(text))
    if has_counter and not has_ours:
        return False
    if has_ours:
        return True
    # bare underscore cell — leave alone (ambiguous party)
    stripped = text.replace("\xa0", " ").strip()
    if _UNDERSCORES.fullmatch(stripped.replace(" ", "")) or re.fullmatch(
        r"[\s_]+", stripped
    ):
        return False
    # other underscore signature-ish lines without clear party
    return bool(_UNDERSCORES.search(text))


def _is_director_signature_target(text: str) -> bool:
    """True for short signature lines / underscore runs — not long prose with должность."""
    if _UNDERSCORES.search(text) or "______" in text:
        return _is_our_signature_line(text)
    # исполнитель.должность without underscores: only short signature-style lines
    if "исполнитель.должность" in text and len(text) <= 180:
        return True
    return False


def _set_paragraph_text(para: Paragraph, new_text: str) -> None:
    """Replace paragraph text (clears run formatting — OK for signature lines)."""
    para.text = new_text


def _process_paragraph(para: Paragraph) -> bool:
    """Inject placeholders into one paragraph. Returns True if changed."""
    text = para.text
    if not text:
        return False
    original = text
    changed = False

    # Stamp near М.П.
    if "М.П" in text and PH_STAMP not in text:
        text = text.rstrip() + f" {PH_STAMP}"
        changed = True

    # Director on signature / short должность lines
    if PH_DIR not in text and _is_director_signature_target(text):
        if _UNDERSCORES.search(text):
            text = _UNDERSCORES.sub(f" {PH_DIR} ", text)
            text = re.sub(r"[ \t]{2,}", "  ", text)
        else:
            text = text.rstrip() + f" {PH_DIR}"
        changed = True

    if changed and text != original:
        _set_paragraph_text(para, text)
        return True
    return False


def _iter_table_paragraphs(table, seen_tc: list, seen_p: list):
    """Yield paragraphs from a table; skip duplicate merged cells via identity."""
    for row in table.rows:
        for cell in row.cells:
            tc = cell._tc
            if any(tc is x for x in seen_tc):
                continue
            seen_tc.append(tc)
            for para in cell.paragraphs:
                el = para._element
                if any(el is x for x in seen_p):
                    continue
                seen_p.append(el)
                yield para
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested, seen_tc, seen_p)


def _iter_paragraphs(doc: Document):
    """Yield unique paragraphs from body and tables (skip duplicate merged cells).

    Uses object identity with strong refs — do NOT use bare id() across
    separately collected proxy objects (addresses can be reused).
    """
    seen_p: list = []
    for para in doc.paragraphs:
        el = para._element
        if any(el is x for x in seen_p):
            continue
        seen_p.append(el)
        yield para
    seen_tc: list = []
    for table in doc.tables:
        yield from _iter_table_paragraphs(table, seen_tc, seen_p)

def _doc_contains(doc: Document, needle: str) -> bool:
    for para in _iter_paragraphs(doc):
        if needle in para.text:
            return True
    return False


def _ensure_allowed_placeholders(doc: Document) -> bool:
    """If ALLOWED template still lacks placeholders, add near the signatory block."""
    has_dir = _doc_contains(doc, PH_DIR)
    has_stamp = _doc_contains(doc, PH_STAMP)
    if has_dir and has_stamp:
        return False
    parts: list[str] = []
    if not has_dir:
        parts.append(PH_DIR)
    if not has_stamp:
        parts.append(f"М.П. {PH_STAMP}")
    block = " ".join(parts)

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def _make_paragraph(text: str):
        p = OxmlElement("w:p")
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = text
        r.append(t)
        p.append(r)
        return p

    # Prefer inserting after last «Главный врач» / signatory-name paragraph
    anchor = None
    for para in doc.paragraphs:
        t = para.text or ""
        if re.search(r"Главный врач|Майдан", t):
            anchor = para
    if anchor is not None:
        anchor._element.addnext(_make_paragraph(block))
        return True

    doc.add_paragraph(block)
    return True


def inject_file(path: Path) -> dict:
    """Process one template. Returns stats dict."""
    name = path.name
    result = {
        "file": name,
        "modified": False,
        "paragraphs_changed": 0,
        "fallback_appended": False,
        "skipped_forbidden": False,
        "missing": False,
    }
    if name in FORBIDDEN:
        result["skipped_forbidden"] = True
        return result
    if name not in ALLOWED and name not in WARN:
        return result
    if not path.exists():
        result["missing"] = True
        return result

    doc = Document(str(path))
    n = 0
    for para in _iter_paragraphs(doc):
        if _process_paragraph(para):
            n += 1
    result["paragraphs_changed"] = n

    if name in ALLOWED:
        if _ensure_allowed_placeholders(doc):
            result["fallback_appended"] = True
            n += 1

    if n > 0 or result["fallback_appended"]:
        doc.save(str(path))
        result["modified"] = True
    return result


def verify() -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for name in sorted(ALLOWED):
        path = TEMPLATES_DIR / name
        doc = Document(str(path))
        has_dir = _doc_contains(doc, PH_DIR)
        has_stamp = _doc_contains(doc, PH_STAMP)
        status = "OK" if (has_dir and has_stamp) else "FAIL"
        if status == "FAIL":
            ok = False
        lines.append(
            f"ALLOWED {name}: director={has_dir} stamp={has_stamp} → {status}"
        )
    for name in sorted(FORBIDDEN):
        path = TEMPLATES_DIR / name
        if not path.exists():
            lines.append(f"FORBIDDEN {name}: MISSING FILE")
            ok = False
            continue
        doc = Document(str(path))
        has_dir = _doc_contains(doc, PH_DIR)
        has_stamp = _doc_contains(doc, PH_STAMP)
        status = "OK" if (not has_dir and not has_stamp) else "FAIL"
        if status == "FAIL":
            ok = False
        lines.append(
            f"FORBIDDEN {name}: director={has_dir} stamp={has_stamp} → {status}"
        )
    return ok, lines


def main() -> int:
    print(f"Templates dir: {TEMPLATES_DIR}")
    print(
        "Conditional {% %} blocks: SKIPPED "
        "(forbidden by template_security.py MSG_STATEMENTS)"
    )
    print()

    targets = sorted(ALLOWED | WARN)
    results = []
    for name in targets:
        path = TEMPLATES_DIR / name
        r = inject_file(path)
        results.append(r)
        flag = (
            "FORBIDDEN-skip"
            if r["skipped_forbidden"]
            else (
                "MISSING"
                if r["missing"]
                else (
                    f"modified paras={r['paragraphs_changed']}"
                    + (" +fallback" if r["fallback_appended"] else "")
                    if r["modified"]
                    else "unchanged"
                )
            )
        )
        print(f"  {name}: {flag}")

    # Ensure we never touched FORBIDDEN
    for name in sorted(FORBIDDEN):
        path = TEMPLATES_DIR / name
        if path.exists():
            # re-read only — no write
            pass
        print(f"  {name}: left untouched (FORBIDDEN)")

    print()
    ok, lines = verify()
    print("Verification:")
    for line in lines:
        print(f"  {line}")
    print()
    modified = [r["file"] for r in results if r["modified"]]
    print(f"Modified ({len(modified)}):")
    for m in modified:
        print(f"  - {m}")
    print()
    print(
        f"Conditional blocks skipped due to security: {SKIP_CONDITIONAL_BLOCKS}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
