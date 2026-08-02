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


_templates_cache: tuple[float, list[dict]] | None = None


def invalidate_templates_cache() -> None:
    global _templates_cache
    _templates_cache = None


def list_templates() -> list[dict]:
    """Каталог шаблонов с кэшем по mtime каталога (без открытия каждого DOCX на каждый запрос)."""
    global _templates_cache
    ensure_core_on_path()
    from docfiller_core.filler import describe_template

    root = templates_dir()
    try:
        stamp = root.stat().st_mtime
    except OSError:
        stamp = 0.0
    if _templates_cache is not None and _templates_cache[0] == stamp:
        return _templates_cache[1]

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
    _templates_cache = (stamp, items)
    return items


def template_path(name: str) -> Path:
    """Безопасный путь к общему шаблону (без path traversal)."""
    safe = Path(name).name
    if safe != name or not safe.endswith(".docx"):
        raise FileNotFoundError("Шаблон не найден")
    path = templates_dir() / safe
    if not path.is_file():
        raise FileNotFoundError("Шаблон не найден")
    return path


def resolve_template_path(name: str, org_id: int | None = None) -> Path:
    """Путь к шаблону: сначала каталог организации, затем общие Шаблоны."""
    safe = Path(name).name
    if safe != name or not safe.endswith(".docx"):
        raise FileNotFoundError("Шаблон не найден")
    if org_id is not None:
        from app.services.org_templates import org_templates_dir

        org_path = org_templates_dir(org_id) / safe
        if org_path.is_file():
            return org_path
    return template_path(safe)


def list_templates_for_org(org_id: int) -> list[dict]:
    """Общие шаблоны + свои шаблоны организации (свои выше при совпадении имени)."""
    from app.services.org_templates import list_org_templates

    shared = []
    for item in list_templates():
        shared.append({**item, "source": "shared", "title": item["stem"].replace("_", " ")})
    org_items = list_org_templates(org_id)
    org_names = {i["name"] for i in org_items}
    # свои перекрывают одноимённые общие в списке
    merged = [i for i in shared if i["name"] not in org_names] + org_items
    merged.sort(key=lambda x: (0 if x.get("source") == "org" else 1, x["name"].lower()))
    return merged


def template_variables(
    name: str,
    requisites: dict | None = None,
    *,
    org_id: int | None = None,
) -> list[str]:
    ensure_core_on_path()
    from docfiller_core.config import settings_from_dict
    from docfiller_core.filler import list_template_variables

    settings = settings_from_dict(requisites or {})
    return list_template_variables(resolve_template_path(name, org_id), settings=settings)


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

    src = resolve_template_path(template_name, org.id)
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
    """Абсолютный путь к файлу документа строго внутри FILES_ROOT/{org_id}."""
    from app.services.safe_paths import resolve_under_org

    return resolve_under_org(doc.org_id, doc.file_path)
