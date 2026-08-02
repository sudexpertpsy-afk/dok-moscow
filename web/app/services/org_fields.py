"""Словарь пользовательских полей организации (W-41)."""

from __future__ import annotations

import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OrgField, OrgFieldType
from app.services.templates import ensure_core_on_path

FIELD_NAME_RE = re.compile(r"^[а-яёa-z][а-яёa-z0-9_]{1,63}$")

FIELD_TYPE_LABELS = {
    OrgFieldType.string: "Строка",
    OrgFieldType.multiline: "Многострочный",
    OrgFieldType.date: "Дата",
    OrgFieldType.money: "Деньги",
    OrgFieldType.checkbox: "Флажок",
    OrgFieldType.select: "Выбор из списка",
    OrgFieldType.counter: "Счётчик",
}

RESERVED_FIELD_NAMES = frozenset({"настройки", "организация"})


class OrgFieldError(Exception):
    pass


def validate_field_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        raise OrgFieldError("Укажите имя плейсхолдера")
    if not FIELD_NAME_RE.fullmatch(raw):
        raise OrgFieldError(
            "Имя плейсхолдера: строчные буквы (а–я/a–z), цифры и «_», "
            "2–64 символа, без точек (например: срок_гарантии)."
        )
    if raw in RESERVED_FIELD_NAMES:
        raise OrgFieldError(f"Имя «{raw}» зарезервировано системой")
    ensure_core_on_path()
    from docfiller_core.system_fields import is_standard_field

    if is_standard_field(raw):
        raise OrgFieldError(
            f"«{raw}» — стандартное поле сервиса; задавать в словаре организации не нужно"
        )
    return raw


def parse_field_type(raw: str) -> OrgFieldType:
    key = (raw or "string").strip().lower()
    try:
        return OrgFieldType(key)
    except ValueError as exc:
        raise OrgFieldError(f"Неизвестный тип поля: {raw}") from exc


def parse_options(raw: str | list | None) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        vals = [str(x).strip() for x in raw if str(x).strip()]
    else:
        text = str(raw).replace("\r\n", "\n")
        parts = re.split(r"[\n,;]+", text)
        vals = [p.strip() for p in parts if p.strip()]
    return vals or None


def list_org_fields(db: Session, org_id: int) -> list[OrgField]:
    return list(
        db.scalars(
            select(OrgField)
            .where(OrgField.org_id == org_id)
            .order_by(OrgField.name.asc())
        ).all()
    )


def get_org_field(db: Session, org_id: int, name: str) -> OrgField | None:
    return db.scalar(
        select(OrgField).where(OrgField.org_id == org_id, OrgField.name == name)
    )


def org_field_map(db: Session, org_id: int) -> dict[str, OrgField]:
    return {f.name: f for f in list_org_fields(db, org_id)}


def field_to_dict(field: OrgField) -> dict[str, Any]:
    return {
        "id": field.id,
        "name": field.name,
        "label": field.label,
        "type": field.field_type.value,
        "type_label": FIELD_TYPE_LABELS.get(field.field_type, field.field_type.value),
        "required": bool(field.required),
        "default": field.default_value or "",
        "hint": field.hint or "",
        "options": list(field.options or []),
    }


def create_org_field(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    name: str,
    label: str,
    field_type: str | OrgFieldType,
    required: bool = False,
    default: str = "",
    hint: str = "",
    options: str | list | None = None,
) -> OrgField:
    safe_name = validate_field_name(name)
    if get_org_field(db, org_id, safe_name):
        raise OrgFieldError(f"Поле «{safe_name}» уже есть в словаре организации")
    ftype = (
        field_type
        if isinstance(field_type, OrgFieldType)
        else parse_field_type(str(field_type))
    )
    lbl = (label or "").strip() or safe_name.replace("_", " ").capitalize()
    opts = parse_options(options)
    if ftype == OrgFieldType.select and not opts:
        raise OrgFieldError("Для типа «выбор из списка» укажите варианты")
    row = OrgField(
        org_id=org_id,
        name=safe_name,
        label=lbl[:255],
        field_type=ftype,
        required=bool(required),
        default_value=(default or "")[:2000],
        hint=(hint or "")[:500],
        options=opts,
        created_by_user_id=user_id,
    )
    db.add(row)
    db.flush()
    return row


def update_org_field(
    db: Session,
    field: OrgField,
    *,
    label: str | None = None,
    field_type: str | OrgFieldType | None = None,
    required: bool | None = None,
    default: str | None = None,
    hint: str | None = None,
    options: str | list | None = None,
) -> OrgField:
    """Правка подписей/типа; переименование запрещено."""
    if label is not None:
        lbl = label.strip()
        if not lbl:
            raise OrgFieldError("Подпись не может быть пустой")
        field.label = lbl[:255]
    if field_type is not None:
        ftype = (
            field_type
            if isinstance(field_type, OrgFieldType)
            else parse_field_type(str(field_type))
        )
        field.field_type = ftype
    if required is not None:
        field.required = bool(required)
    if default is not None:
        field.default_value = (default or "")[:2000]
    if hint is not None:
        field.hint = (hint or "")[:500]
    if options is not None or (
        field_type is not None and field.field_type == OrgFieldType.select
    ):
        opts = parse_options(options if options is not None else field.options)
        if field.field_type == OrgFieldType.select and not opts:
            raise OrgFieldError("Для типа «выбор из списка» укажите варианты")
        field.options = opts
    db.flush()
    return field


def templates_using_field(org_id: int, name: str) -> list[str]:
    """Имена org-DOCX, в которых встречается плейсхолдер (по безопасному парсингу)."""
    ensure_core_on_path()
    from docfiller_core.template_security import (
        TemplateSecurityError,
        analyze_docx_template,
    )
    from app.services.org_templates import org_templates_dir

    used: list[str] = []
    root = org_templates_dir(org_id)
    for path in sorted(root.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        try:
            variables = analyze_docx_template(path).variables
        except TemplateSecurityError:
            # битый/старый шаблон — считаем, что поле может использоваться
            used.append(path.name)
            continue
        except Exception:
            used.append(path.name)
            continue
        if name in variables:
            used.append(path.name)
    return used


def delete_org_field(db: Session, field: OrgField) -> None:
    used = templates_using_field(field.org_id, field.name)
    if used:
        shown = ", ".join(used[:5])
        more = f" и ещё {len(used) - 5}" if len(used) > 5 else ""
        raise OrgFieldError(
            f"Поле «{field.name}» используется в шаблонах: {shown}{more}. "
            "Сначала уберите плейсхолдер из DOCX или удалите шаблоны."
        )
    db.delete(field)
    db.flush()


def classify_template_variables(
    db: Session,
    org_id: int,
    variables: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Разложить переменные шаблона: standard / org / new."""
    ensure_core_on_path()
    from docfiller_core.system_fields import get_standard_field, is_standard_field

    org_map = org_field_map(db, org_id)
    standard: list[dict[str, Any]] = []
    org_known: list[dict[str, Any]] = []
    new_fields: list[dict[str, Any]] = []

    for var in variables:
        if is_standard_field(var):
            std = get_standard_field(var) or {"name": var, "label": var}
            standard.append(std)
        elif var in org_map:
            org_known.append(field_to_dict(org_map[var]))
        else:
            if not FIELD_NAME_RE.fullmatch(var):
                raise OrgFieldError(
                    f"Поле «{var}» имеет недопустимое имя. "
                    "Используйте snake_case без точек "
                    "(строчные буквы, цифры и «_», без точек)."
                )
            if var in RESERVED_FIELD_NAMES:
                raise OrgFieldError(f"Имя «{var}» зарезервировано системой")
            new_fields.append(
                {
                    "name": var,
                    "label": var.replace("_", " ").capitalize(),
                    "type": "string",
                    "required": False,
                    "default": "",
                    "hint": "",
                    "options": [],
                }
            )
    return {"standard": standard, "org": org_known, "new": new_fields}


def staging_dir(org_id: int) -> Path:
    from app.services.safe_paths import org_files_root, resolve_under

    root = resolve_under(org_files_root(org_id), "templates", ".staging")
    root.mkdir(parents=True, exist_ok=True)
    return root


def stage_org_upload(*, org_id: int, filename: str, data: bytes) -> tuple[str, str]:
    """Сохранить DOCX во временный staging; вернуть (token, safe_name)."""
    from app.services.docx_upload import DocxUploadError, validate_docx_bytes
    from app.services.org_templates import OrgTemplateError, _safe_stem
    from app.services.safe_paths import resolve_under

    ensure_core_on_path()
    from docfiller_core.template_security import (
        TemplateSecurityError,
        assert_safe_docx_template,
    )

    try:
        validate_docx_bytes(data)
    except DocxUploadError as exc:
        raise OrgFieldError(str(exc)) from exc
    try:
        assert_safe_docx_template(data)
    except TemplateSecurityError as exc:
        raise OrgFieldError(str(exc)) from exc

    try:
        stem = _safe_stem(filename)
    except OrgTemplateError as exc:
        raise OrgFieldError(str(exc)) from exc
    name = f"{stem}.docx"
    token = secrets.token_urlsafe(16)
    dest = resolve_under(staging_dir(org_id), f"{token}.docx")
    dest.write_bytes(data)
    meta = resolve_under(staging_dir(org_id), f"{token}.name")
    meta.write_text(name, encoding="utf-8")
    return token, name


def read_staged(org_id: int, token: str) -> tuple[bytes, str]:
    from app.services.safe_paths import resolve_under

    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token):
        raise OrgFieldError("Некорректный сеанс загрузки")
    path = resolve_under(staging_dir(org_id), f"{token}.docx")
    meta = resolve_under(staging_dir(org_id), f"{token}.name")
    if not path.is_file() or not meta.is_file():
        raise OrgFieldError("Сеанс загрузки устарел — загрузите файл снова")
    return path.read_bytes(), meta.read_text(encoding="utf-8").strip()


def clear_staged(org_id: int, token: str) -> None:
    from app.services.safe_paths import resolve_under

    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token):
        return
    for suffix in (".docx", ".name"):
        p = resolve_under(staging_dir(org_id), f"{token}{suffix}")
        if p.is_file():
            p.unlink(missing_ok=True)


def commit_staged_upload(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    token: str,
    contract_type: str,
    new_field_defs: list[dict[str, Any]],
    replace_existing: bool = False,
) -> str:
    """Создать новые поля и сохранить шаблон из staging."""
    from app.services.org_templates import (
        OrgTemplateError,
        delete_org_template,
        save_org_upload,
    )

    data, name = read_staged(org_id, token)
    ensure_core_on_path()
    from docfiller_core.template_security import assert_safe_docx_template

    variables = assert_safe_docx_template(data)
    classified = classify_template_variables(db, org_id, variables)
    needed_new = {f["name"] for f in classified["new"]}
    provided = {str(d.get("name") or "").strip() for d in new_field_defs}
    if needed_new - provided:
        missing = ", ".join(sorted(needed_new - provided))
        raise OrgFieldError(
            f"Нельзя сохранить шаблон с неопределёнными полями: {missing}"
        )
    if provided - needed_new:
        # лишние определения игнорируем только если имя не из needed — отвергаем путаницу
        extra = ", ".join(sorted(provided - needed_new))
        raise OrgFieldError(f"Лишние определения полей: {extra}")

    for defn in new_field_defs:
        create_org_field(
            db,
            org_id=org_id,
            user_id=user_id,
            name=str(defn.get("name") or ""),
            label=str(defn.get("label") or ""),
            field_type=str(defn.get("type") or "string"),
            required=bool(defn.get("required")),
            default=str(defn.get("default") or ""),
            hint=str(defn.get("hint") or ""),
            options=defn.get("options"),
        )

    from app.services.org_templates import org_templates_dir, resolve_org_template_file
    from app.services.safe_paths import resolve_under

    dest_check = resolve_under(org_templates_dir(org_id), name)
    if dest_check.exists():
        if not replace_existing:
            raise OrgFieldError(
                f"Шаблон «{name}» уже есть — отметьте «Заменить» или удалите старый"
            )
        try:
            delete_org_template(org_id=org_id, name=name)
        except FileNotFoundError:
            pass

    try:
        saved = save_org_upload(
            org_id=org_id,
            filename=name,
            data=data,
            contract_type=contract_type or "",
        )
    except OrgTemplateError as exc:
        raise OrgFieldError(str(exc)) from exc

    clear_staged(org_id, token)
    return saved


def meta_from_org_field(field: OrgField) -> dict[str, Any]:
    return {
        "name": field.name,
        "label": field.label,
        "hint": field.hint or "",
        "multiline": field.field_type == OrgFieldType.multiline,
        "is_date": field.field_type == OrgFieldType.date,
        "is_number": field.field_type == OrgFieldType.counter,
        "is_money": field.field_type == OrgFieldType.money,
        "is_checkbox": field.field_type == OrgFieldType.checkbox,
        "is_select": field.field_type == OrgFieldType.select,
        "options": list(field.options or []),
        "required": bool(field.required),
        "org_field": True,
        "history": False,
        "dadata": None,
        "counter_key": f"orgf_{field.name}" if field.field_type == OrgFieldType.counter else None,
    }


def write_temp_docx(data: bytes) -> Path:
    fd, path = tempfile.mkstemp(suffix=".docx")
    Path(path).write_bytes(data)
    import os

    os.close(fd)
    return Path(path)
