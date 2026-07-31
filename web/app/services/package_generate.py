"""Генерация комплекта документов и архивов."""

from __future__ import annotations

import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Counterparty, CounterpartySource, Document, DocumentFormat, Organization
from app.services.counters import allocate_number
from app.services.gotenberg import GotenbergError, convert_docx_to_pdf, merge_pdfs
from app.services.package_master import (
    build_contexts,
    counterparty_from_core,
    selected_templates,
)
from app.services.templates import absolute_file, generate_docx, org_month_dir

_NUMBER_FIELDS = {
    "номер_договора": "dogovor",
    "номер_счёта": "schet",
    "номер_акта": "akt",
    "номер_пко": "pko",
    "номер_допсоглашения": "dopsogl",
}


def _autofill_numbers(db: Session, org_id: int, context: dict) -> dict:
    ctx = dict(context)
    for field, key in _NUMBER_FIELDS.items():
        if field not in ctx:
            continue
        raw = str(ctx.get(field) or "").strip()
        if not raw or raw.lower() in {"auto", "авто", "+"}:
            _, formatted = allocate_number(db, org_id, key)
            ctx[field] = formatted
    return ctx


def upsert_counterparty(
    db: Session,
    org_id: int,
    тип: str,
    core_values: dict,
    existing_id: int | None,
) -> Counterparty:
    fields = counterparty_from_core(тип, core_values)
    if existing_id:
        cp = db.get(Counterparty, existing_id)
        if cp is None or cp.org_id != org_id:
            raise ValueError("Контрагент не найден")
        for k, v in fields.items():
            if k == "type":
                continue
            if v:
                setattr(cp, k, v)
        db.flush()
        return cp

    cp = Counterparty(
        org_id=org_id,
        type=fields["type"],
        name=fields.get("name") or None,
        fio=fields.get("fio") or None,
        inn=fields.get("inn") or None,
        kpp=fields.get("kpp") or None,
        ogrn=fields.get("ogrn") or None,
        address=fields.get("address") or None,
        phone=fields.get("phone") or None,
        email=fields.get("email") or None,
        source=CounterpartySource.manual,
    )
    db.add(cp)
    db.flush()
    return cp


def generate_package(
    *,
    db: Session,
    org: Organization,
    user_id: int,
    тип: str,
    contract_template: str,
    extras: list[str],
    core_values: dict,
    additional_values: dict,
    counterparty_id: int | None,
) -> dict:
    """Сгенерировать все документы комплекта, вернуть метаданные."""
    selected = selected_templates(contract_template, extras)
    if not selected:
        raise ValueError("Не выбран ни один шаблон")

    merged = {**core_values, **additional_values}
    merged = _autofill_numbers(db, org.id, merged)
    # синхронизируем core/additional после автонумерации
    core_values = {k: merged.get(k, v) for k, v in core_values.items()}
    additional_values = {k: merged.get(k, v) for k, v in additional_values.items()}
    additional_values.update({k: v for k, v in merged.items() if k not in core_values})

    cp = upsert_counterparty(db, org.id, тип, core_values, counterparty_id)
    contexts = build_contexts(selected, core_values, additional_values)

    docs: list[Document] = []
    for tpl in selected:
        ctx = contexts.get(tpl) or {}
        # номера уже в merged — прокинуть
        for k, v in merged.items():
            if k not in ctx or not ctx[k]:
                ctx[k] = v
        number = None
        for field in _NUMBER_FIELDS:
            if ctx.get(field):
                number = str(ctx[field])
                break
        doc = generate_docx(
            db=db,
            org=org,
            user_id=user_id,
            template_name=tpl,
            context=ctx,
            number=number,
        )
        doc.counterparty_id = cp.id
        db.add(doc)
        docs.append(doc)
    db.commit()
    for d in docs:
        db.refresh(d)

    return {
        "counterparty_id": cp.id,
        "document_ids": [d.id for d in docs],
        "documents": docs,
        "core_values": core_values,
        "additional_values": additional_values,
        "selected": selected,
    }


def build_zip(docs: list[Document], zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            path = absolute_file(doc)
            if path.is_file():
                zf.write(path, arcname=path.name)
    return zip_path


def build_merged_pdf(docs: list[Document], pdf_path: Path) -> Path:
    pdfs: list[Path] = []
    for doc in docs:
        src = absolute_file(doc)
        if doc.format == DocumentFormat.pdf:
            pdfs.append(src)
            continue
        out = src.with_suffix(".pdf")
        if not out.is_file():
            convert_docx_to_pdf(src, out)
        pdfs.append(out)
    return merge_pdfs(pdfs, pdf_path)


def package_export_dir(org_id: int) -> Path:
    return org_month_dir(org_id)
