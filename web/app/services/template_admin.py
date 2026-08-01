"""Админское управление файлами шаблонов DOCX (W-admin-templates)."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.services.templates import (
    ensure_core_on_path,
    invalidate_templates_cache,
    template_path,
    templates_dir,
)

CONTRACT_TYPE_LABELS = {
    "": "Не договор комплекта",
    "Физлицо": "Договор — физлицо",
    "Юрлицо": "Договор — юрлицо",
    "Эксперт (ГПД)": "Договор — эксперт (ГПД)",
}


class TemplateAdminError(Exception):
    pass


def _root() -> Path:
    root = templates_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_stem(raw: str) -> str:
    text = (raw or "").strip().replace("\\", "/").split("/")[-1]
    if text.lower().endswith(".docx"):
        text = text[: -len(".docx")]
    text = text.replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("._")
    if not text or ".." in text or "/" in text or "\\" in text or "\0" in text:
        raise TemplateAdminError("Недопустимое имя файла")
    if len(text) > 180:
        raise TemplateAdminError("Слишком длинное имя")
    if not re.fullmatch(r"[\w.\-]+", text, flags=re.UNICODE):
        raise TemplateAdminError(
            "Имя может содержать буквы, цифры, пробел, точку, дефис и подчёркивание"
        )
    return text


def _docx_name(stem: str) -> str:
    return f"{stem}.docx"


def display_title(filename: str) -> str:
    return Path(filename).stem.replace("_", " ")


def list_admin_templates() -> list[dict]:
    ensure_core_on_path()
    from docfiller_core.filler import describe_template
    from docfiller_core.registry import load_registry

    root = _root()
    registry = load_registry(root)
    contract_of: dict[str, list[str]] = {}
    for ctype, names in registry.get("contracts", {}).items():
        for name in names:
            contract_of.setdefault(name, []).append(ctype)

    items: list[dict] = []
    for path in sorted(root.glob("*.docx"), key=lambda p: p.name.lower()):
        if path.name.startswith("~$"):
            continue
        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            size, mtime = 0, 0.0
        try:
            description = describe_template(path) or display_title(path.name)
        except Exception:
            description = display_title(path.name)
        items.append(
            {
                "name": path.name,
                "title": display_title(path.name),
                "description": description,
                "size": size,
                "mtime": mtime,
                "contract_types": contract_of.get(path.name, []),
                "is_contract": path.name.startswith("Договор_"),
            }
        )
    return items


def save_upload(
    *,
    filename: str,
    data: bytes,
    contract_type: str = "",
) -> str:
    if not data:
        raise TemplateAdminError("Пустой файл")
    if len(data) > 25 * 1024 * 1024:
        raise TemplateAdminError("Файл больше 25 МБ")
    if not data[:2] == b"PK":
        raise TemplateAdminError("Нужен файл .docx (Office Open XML)")
    stem = _safe_stem(filename)
    name = _docx_name(stem)
    dest = _root() / name
    if dest.exists():
        raise TemplateAdminError(f"Шаблон «{name}» уже есть — переименуйте или удалите старый")
    dest.write_bytes(data)
    if contract_type:
        ensure_core_on_path()
        from docfiller_core.registry import CONTRACT_TYPES, add_contract

        if contract_type not in CONTRACT_TYPES:
            raise TemplateAdminError("Неверный тип договора для комплекта")
        add_contract(_root(), name, contract_type)
    invalidate_templates_cache()
    return name


def rename_template(db: Session, *, old_name: str, new_title: str) -> str:
    ensure_core_on_path()
    from docfiller_core.registry import rename_in_registry

    old = Path(old_name).name
    if old != old_name or not old.endswith(".docx"):
        raise TemplateAdminError("Некорректное имя шаблона")
    src = template_path(old)
    new_stem = _safe_stem(new_title)
    new_name = _docx_name(new_stem)
    if new_name == old:
        return old
    dest = _root() / new_name
    if dest.exists():
        raise TemplateAdminError(f"Файл «{new_name}» уже существует")
    src.rename(dest)
    rename_in_registry(_root(), old, new_name)
    _rewrite_db_references(db, old, new_name)
    _rewrite_org_packages(db, old, new_name)
    invalidate_templates_cache()
    return new_name


def delete_template(db: Session, *, name: str) -> None:
    ensure_core_on_path()
    from docfiller_core.registry import remove_from_registry

    safe = Path(name).name
    if safe != name or not safe.endswith(".docx"):
        raise TemplateAdminError("Некорректное имя шаблона")
    path = template_path(safe)
    path.unlink()
    remove_from_registry(_root(), safe)
    invalidate_templates_cache()


def set_contract_membership(*, name: str, contract_type: str) -> None:
    """Привязать/отвязать шаблон как договор комплекта."""
    ensure_core_on_path()
    from docfiller_core.registry import CONTRACT_TYPES, load_registry, save_registry

    safe = Path(name).name
    template_path(safe)  # existence
    data = load_registry(_root())
    for ctype in CONTRACT_TYPES:
        data["contracts"][ctype] = [x for x in data["contracts"][ctype] if x != safe]
    if contract_type:
        if contract_type not in CONTRACT_TYPES:
            raise TemplateAdminError("Неверный тип договора")
        data["contracts"][contract_type].append(safe)
    save_registry(_root(), data)
    invalidate_templates_cache()


def _rewrite_db_references(db: Session, old: str, new: str) -> None:
    from app.models import Contract, Document

    for doc in db.scalars(select(Document).where(Document.template == old)).all():
        doc.template = new
    for ctr in db.scalars(select(Contract).where(Contract.template == old)).all():
        ctr.template = new
    db.flush()


def _rewrite_org_packages(db: Session, old: str, new: str) -> None:
    from app.models import Organization

    for org in db.scalars(select(Organization)).all():
        req = org.requisites or {}
        if not isinstance(req, dict):
            continue
        packages = req.get("пакеты")
        if not isinstance(packages, dict) or not packages:
            continue
        changed = False
        updated: dict = {}
        for key, docs in packages.items():
            new_key = new if key == old else key
            if isinstance(docs, list):
                new_docs = [new if d == old else d for d in docs]
                if new_docs != docs or new_key != key:
                    changed = True
                updated[new_key] = new_docs
            else:
                updated[new_key] = docs
                if new_key != key:
                    changed = True
        if changed:
            req = dict(req)
            req["пакеты"] = updated
            org.requisites = req
            flag_modified(org, "requisites")
    db.flush()


def ensure_writable_templates_dir() -> tuple[bool, str]:
    root = _root()
    probe = root / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, str(root)
    except OSError as exc:
        return False, f"{root}: {exc}"
