"""Свои шаблоны организации: files_root/{org_id}/templates/."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contract, Document, TariffCode
from app.services.billing import get_tariff_limits
from app.services.docx_upload import (
    MAX_ORG_TEMPLATES,
    DocxUploadError,
    validate_docx_bytes,
)
from app.services.templates import ensure_core_on_path

CONTRACT_TYPE_LABELS = {
    "": "Не договор комплекта",
    "Физлицо": "Договор — физлицо",
    "Юрлицо": "Договор — юрлицо",
    "Эксперт (ГПД)": "Договор — эксперт (ГПД)",
}
CONTRACT_TYPES = ("Физлицо", "Юрлицо", "Эксперт (ГПД)")
REGISTRY_NAME = "contracts_registry.json"


class OrgTemplateError(Exception):
    pass


def org_templates_dir(org_id: int) -> Path:
    from app.services.safe_paths import org_files_root, resolve_under

    root = resolve_under(org_files_root(org_id), "templates")
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_org_template_file(org_id: int, name: str) -> Path:
    """Путь к файлу шаблона строго внутри каталога templates организации."""
    from app.services.safe_paths import resolve_under

    safe = Path(name).name
    if safe != name or not safe.endswith(".docx"):
        raise OrgTemplateError("Некорректное имя шаблона")
    path = resolve_under(org_templates_dir(org_id), safe)
    if not path.is_file():
        raise FileNotFoundError("Шаблон не найден")
    return path


def can_manage_org_templates(db: Session, org_id: int) -> tuple[bool, str | None]:
    """Свои шаблоны — на тарифе «Организация» при активной подписке."""
    from app.services.billing import ensure_beta_subscriptions

    ensure_beta_subscriptions(db)
    limits = get_tariff_limits(db, org_id)
    if not limits.is_current:
        return False, "Подписка неактивна. Оплатите тариф «Организация», чтобы управлять своими шаблонами."
    if limits.tariff_code != TariffCode.organization:
        return (
            False,
            f"Свои шаблоны доступны на тарифе «Организация». Сейчас: «{limits.tariff_name}».",
        )
    return True, None


def assert_can_manage_org_templates(db: Session, org_id: int) -> None:
    from fastapi import HTTPException, status
    from urllib.parse import quote

    ok, reason = can_manage_org_templates(db, org_id)
    if ok:
        return
    q = quote(reason or "Нужен тариф Организация")
    dest = f"/cabinet/billing/?error={q}"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail=reason,
        headers={"Location": dest, "HX-Redirect": dest},
    )


def _safe_stem(raw: str) -> str:
    text = (raw or "").strip().replace("\\", "/").split("/")[-1]
    if text.lower().endswith(".docx"):
        text = text[: -len(".docx")]
    text = text.replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("._")
    if not text or ".." in text or "/" in text or "\\" in text or "\0" in text:
        raise OrgTemplateError("Недопустимое имя файла")
    if len(text) > 180:
        raise OrgTemplateError("Слишком длинное имя")
    if not re.fullmatch(r"[\w.\-]+", text, flags=re.UNICODE):
        raise OrgTemplateError(
            "Имя может содержать буквы, цифры, пробел, точку, дефис и подчёркивание"
        )
    return text


def display_title(filename: str) -> str:
    return Path(filename).stem.replace("_", " ")


def _registry_path(org_id: int) -> Path:
    return org_templates_dir(org_id) / REGISTRY_NAME


def load_org_registry(org_id: int) -> dict:
    path = _registry_path(org_id)
    data = {"contracts": {k: [] for k in CONTRACT_TYPES}}
    if not path.is_file():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    contracts = raw.get("contracts") if isinstance(raw, dict) else None
    if isinstance(contracts, dict):
        for key in CONTRACT_TYPES:
            vals = contracts.get(key)
            if isinstance(vals, list):
                data["contracts"][key] = [
                    str(x) for x in vals if str(x).endswith(".docx")
                ]
    return data


def save_org_registry(org_id: int, data: dict) -> None:
    path = _registry_path(org_id)
    payload = {"contracts": {k: list(data.get("contracts", {}).get(k, [])) for k in CONTRACT_TYPES}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def list_org_templates(org_id: int) -> list[dict]:
    ensure_core_on_path()
    from docfiller_core.filler import describe_template

    root = org_templates_dir(org_id)
    registry = load_org_registry(org_id)
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
            description = describe_template(path) or display_title(path.name)
        except Exception:
            size, mtime, description = 0, 0.0, display_title(path.name)
        items.append(
            {
                "name": path.name,
                "stem": path.stem,
                "title": display_title(path.name),
                "description": description,
                "size": size,
                "mtime": mtime,
                "contract_types": contract_of.get(path.name, []),
                "source": "org",
            }
        )
    return items


def org_contract_names(org_id: int, contract_type: str) -> list[str]:
    data = load_org_registry(org_id)
    names = list(data.get("contracts", {}).get(contract_type) or [])
    root = org_templates_dir(org_id)
    return [n for n in names if (root / n).is_file()]


def save_org_upload(
    *,
    org_id: int,
    filename: str,
    data: bytes,
    contract_type: str = "",
) -> str:
    try:
        validate_docx_bytes(data)
    except DocxUploadError as exc:
        raise OrgTemplateError(str(exc)) from exc
    root = org_templates_dir(org_id)
    existing = [p for p in root.glob("*.docx") if not p.name.startswith("~$")]
    if len(existing) >= MAX_ORG_TEMPLATES:
        raise OrgTemplateError(f"Лимит своих шаблонов: {MAX_ORG_TEMPLATES}")
    from app.services.safe_paths import resolve_under

    stem = _safe_stem(filename)
    name = f"{stem}.docx"
    dest = resolve_under(root, name)
    if dest.exists():
        raise OrgTemplateError(f"Шаблон «{name}» уже есть — переименуйте или удалите старый")
    dest.write_bytes(data)
    if contract_type:
        if contract_type not in CONTRACT_TYPES:
            raise OrgTemplateError("Неверный тип договора для комплекта")
        reg = load_org_registry(org_id)
        lst = reg["contracts"].setdefault(contract_type, [])
        if name not in lst:
            lst.append(name)
        save_org_registry(org_id, reg)
    return name


def rename_org_template(
    db: Session,
    *,
    org_id: int,
    old_name: str,
    new_title: str,
) -> str:
    from app.services.safe_paths import resolve_under

    src = resolve_org_template_file(org_id, old_name)
    old = src.name
    new_name = f"{_safe_stem(new_title)}.docx"
    if new_name == old:
        return old
    dest = resolve_under(org_templates_dir(org_id), new_name)
    if dest.exists():
        raise OrgTemplateError(f"Файл «{new_name}» уже существует")
    src.rename(dest)
    reg = load_org_registry(org_id)
    for key in CONTRACT_TYPES:
        reg["contracts"][key] = [new_name if x == old else x for x in reg["contracts"][key]]
    save_org_registry(org_id, reg)
    for doc in db.scalars(
        select(Document).where(Document.org_id == org_id, Document.template == old)
    ).all():
        doc.template = new_name
    for ctr in db.scalars(
        select(Contract).where(Contract.org_id == org_id, Contract.template == old)
    ).all():
        ctr.template = new_name
    db.flush()
    return new_name


def delete_org_template(*, org_id: int, name: str) -> None:
    path = resolve_org_template_file(org_id, name)
    safe = path.name
    path.unlink()
    reg = load_org_registry(org_id)
    for key in CONTRACT_TYPES:
        reg["contracts"][key] = [x for x in reg["contracts"][key] if x != safe]
    save_org_registry(org_id, reg)


def set_org_contract_role(*, org_id: int, name: str, contract_type: str) -> None:
    path = resolve_org_template_file(org_id, name)
    safe = path.name
    reg = load_org_registry(org_id)
    for ctype in CONTRACT_TYPES:
        reg["contracts"][ctype] = [x for x in reg["contracts"][ctype] if x != safe]
    if contract_type:
        if contract_type not in CONTRACT_TYPES:
            raise OrgTemplateError("Неверный тип договора")
        reg["contracts"][contract_type].append(safe)
    save_org_registry(org_id, reg)
