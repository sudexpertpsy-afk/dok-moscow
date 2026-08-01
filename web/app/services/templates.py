"""Каталог шаблонов и генерация DOCX через ядро Шаблонера."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, DocumentFormat, Organization


def ensure_core_on_path() -> None:
    settings = get_settings()
    core = str(Path(settings.core_path).resolve())
    if core not in sys.path:
        sys.path.insert(0, core)


def templates_dir() -> Path:
    return Path(get_settings().templates_dir)


def list_templates() -> list[dict]:
    ensure_core_on_path()
    from docfiller_core.filler import describe_template

    root = templates_dir()
    items = []
    for path in sorted(root.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        items.append(
            {
                "name": path.name,
                "stem": path.stem,
                "description": describe_template(path) or path.stem.replace("_", " "),
            }
        )
    return items


def template_path(name: str) -> Path:
    """Безопасный путь к шаблону (без path traversal)."""
    safe = Path(name).name
    if safe != name or not safe.endswith(".docx"):
        raise FileNotFoundError("Шаблон не найден")
    path = templates_dir() / safe
    if not path.is_file():
        raise FileNotFoundError("Шаблон не найден")
    return path


def template_variables(name: str, requisites: dict | None = None) -> list[str]:
    ensure_core_on_path()
    from docfiller_core.config import settings_from_dict
    from docfiller_core.filler import list_template_variables

    settings = settings_from_dict(requisites or {})
    return list_template_variables(template_path(name), settings=settings)


def org_month_dir(org_id: int, when: date | None = None) -> Path:
    when = when or date.today()
    root = Path(get_settings().files_root) / str(org_id) / when.strftime("%Y-%m")
    root.mkdir(parents=True, exist_ok=True)
    return root


def generate_docx(
    *,
    db: Session,
    org: Organization,
    user_id: int | None,
    template_name: str,
    context: dict,
    number: str | None = None,
) -> Document:
    """Сгенерировать DOCX, сохранить файл и запись documents."""
    ensure_core_on_path()
    from docfiller_core.filler import fill_template
    from docfiller_core.utils import safe_filename

    src = template_path(template_name)
    stem = safe_filename(number or context.get("номер_договора") or src.stem)
    out_dir = org_month_dir(org.id)
    out_name = f"{stem}.docx"
    # избежать коллизий имён
    out_path = out_dir / out_name
    n = 1
    while out_path.exists():
        out_name = f"{stem}_{n}.docx"
        out_path = out_dir / out_name
        n += 1

    fill_template(src, out_path, context, settings=org.requisites or {})

    rel = str(out_path.relative_to(Path(get_settings().files_root)))
    # не храним ПДн-тяжёлый полный контекст как есть? ТЗ: контекст JSONB — нужен для повтора.
    # Маскируем при логировании, в БД храним как в Шаблонере.
    doc = Document(
        org_id=org.id,
        contract_id=None,
        counterparty_id=None,
        template=template_name,
        number=number or str(context.get("номер_договора") or "") or None,
        file_path=rel,
        format=DocumentFormat.docx,
        context=dict(context),
        created_by=user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def absolute_file(doc: Document) -> Path:
    path = Path(get_settings().files_root) / doc.file_path
    # защита от traversal
    root = Path(get_settings().files_root).resolve()
    resolved = path.resolve()
    if not str(resolved).startswith(str(root)):
        raise FileNotFoundError("Недопустимый путь")
    return resolved
