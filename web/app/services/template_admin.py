"""Админское управление файлами шаблонов DOCX (W-admin-templates)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.services.docx_upload import DocxUploadError, validate_docx_bytes
from app.services.templates import (
    ensure_core_on_path,
    invalidate_templates_cache,
    template_path,
    templates_dir,
)

# Tombstone: git pull / checkout возвращает tracked DOCX из репозитория.
# Список удалённых через админку хранится рядом с файлами и применяется после деплоя.
DELETED_TEMPLATES_FILENAME = ".deleted_templates.json"

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


def deleted_templates_path(root: Path | None = None) -> Path:
    return (root or _root()) / DELETED_TEMPLATES_FILENAME


def load_deleted_templates(root: Path | None = None) -> set[str]:
    path = deleted_templates_path(root)
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(raw, dict):
        names = raw.get("deleted") or raw.get("names") or []
    elif isinstance(raw, list):
        names = raw
    else:
        return set()
    out: set[str] = set()
    for item in names:
        name = Path(str(item)).name
        if name.endswith(".docx") and name == str(item).replace("\\", "/").split("/")[-1]:
            out.add(name)
    return out


def save_deleted_templates(names: set[str], root: Path | None = None) -> None:
    root = root or _root()
    path = deleted_templates_path(root)
    payload = {"deleted": sorted(names)}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def mark_template_deleted(name: str, root: Path | None = None) -> None:
    root = root or _root()
    names = load_deleted_templates(root)
    names.add(Path(name).name)
    save_deleted_templates(names, root)


def unmark_template_deleted(name: str, root: Path | None = None) -> None:
    root = root or _root()
    names = load_deleted_templates(root)
    names.discard(Path(name).name)
    save_deleted_templates(names, root)


def _unlink_template_files(root: Path, name: str) -> bool:
    """Удалить DOCX и соседний .manifest.yaml. True если DOCX был на диске."""
    safe = Path(name).name
    docx = root / safe
    existed = docx.is_file()
    if existed:
        docx.unlink()
    manifest = root / f"{Path(safe).stem}.manifest.yaml"
    if manifest.is_file():
        manifest.unlink()
    return existed


def apply_deleted_templates(root: Path | None = None) -> list[str]:
    """После git pull: убрать tombstone-шаблоны из реестра кабинета.

    W-45: DOCX на диске не удаляем — /obraztsy читает файлы с include_deleted=True;
    кабинет скрывает их через load_deleted_templates.
    """
    ensure_core_on_path()
    from docfiller_core.contracts_registry import remove_from_registry

    root = root or _root()
    touched: list[str] = []
    for name in sorted(load_deleted_templates(root)):
        try:
            remove_from_registry(root, name)
            touched.append(name)
        except OSError:
            pass
    if touched:
        invalidate_templates_cache()
    return touched


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
    from docfiller_core.contracts_registry import load_registry

    root = _root()
    registry = load_registry(root)
    contract_of: dict[str, list[str]] = {}
    for ctype, names in registry.get("contracts", {}).items():
        for name in names:
            contract_of.setdefault(name, []).append(ctype)

    deleted = load_deleted_templates(root)
    items: list[dict] = []
    for path in sorted(root.glob("*.docx"), key=lambda p: p.name.lower()):
        if path.name.startswith("~$"):
            continue
        if path.name in deleted:
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
    try:
        validate_docx_bytes(data)
    except DocxUploadError as exc:
        raise TemplateAdminError(str(exc)) from exc
    ensure_core_on_path()
    from docfiller_core.template_security import (
        TemplateSecurityError,
        assert_safe_docx_template,
    )

    try:
        assert_safe_docx_template(data)
    except TemplateSecurityError as exc:
        raise TemplateAdminError(str(exc)) from exc
    stem = _safe_stem(filename)
    name = _docx_name(stem)
    root = _root()
    dest = root / name
    deleted = load_deleted_templates(root)
    if dest.exists() and name not in deleted:
        raise TemplateAdminError(f"Шаблон «{name}» уже есть — переименуйте или удалите старый")
    # повторная загрузка после удаления (в т.ч. если git вернул файл) — снимаем tombstone
    dest.write_bytes(data)
    unmark_template_deleted(name, root)
    if contract_type:
        ensure_core_on_path()
        from docfiller_core.contracts_registry import CONTRACT_TYPES, add_contract

        if contract_type not in CONTRACT_TYPES:
            raise TemplateAdminError("Неверный тип договора для комплекта")
        add_contract(_root(), name, contract_type)
    invalidate_templates_cache()
    return name


def rename_template(db: Session, *, old_name: str, new_title: str) -> str:
    ensure_core_on_path()
    from docfiller_core.contracts_registry import rename_in_registry

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
    """Скрыть шаблон в кабинете (tombstone + реестр). Файл на диске оставляем (W-45 /obraztsy)."""
    ensure_core_on_path()
    from docfiller_core.contracts_registry import remove_from_registry

    safe = Path(name).name
    if safe != name or not safe.endswith(".docx"):
        raise TemplateAdminError("Некорректное имя шаблона")
    root = _root()
    # existence check
    template_path(safe)
    mark_template_deleted(safe, root)
    remove_from_registry(root, safe)
    invalidate_templates_cache()


def set_contract_membership(*, name: str, contract_type: str) -> None:
    """Привязать/отвязать шаблон как договор комплекта."""
    ensure_core_on_path()
    from docfiller_core.contracts_registry import CONTRACT_TYPES, load_registry, save_registry

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
