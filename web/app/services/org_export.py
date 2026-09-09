"""W-49 C: полная выгрузка данных организации (dok-export-v1)."""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CalendarEvent,
    Contract,
    Counter,
    Counterparty,
    Document,
    Event,
    LawBookmark,
    LawNote,
    Organization,
    OrgField,
    OrgRole,
    Payment,
    User,
    UserRole,
    utcnow,
)
from app.services.audit import record_event
from app.services.branding import branding_dir
from app.services.mail import send_email
from app.services.org_templates import org_templates_dir
from app.services.rate_counters import get_count, incr_counter, set_if_absent
from app.services.safe_paths import org_files_root
from app.services.templates import absolute_file

log = logging.getLogger("dok.org_export")

FORMAT_VERSION = "dok-export-v1"
DAILY_LIMIT = 2
TTL_HOURS = 24

SECRET_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "fernet",
    "totp",
    "hash",
)


class ExportError(ValueError):
    """Понятная ошибка выгрузки для UI."""


def _day_window_start() -> datetime:
    now = utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _rate_key(org_id: int) -> str:
    return f"org_export:{org_id}:{_day_window_start().strftime('%Y%m%d')}"


def check_export_rate(db: Session, org_id: int) -> None:
    key = _rate_key(org_id)
    set_if_absent(db, key, window_start=_day_window_start(), count=0)
    if get_count(db, key) >= DAILY_LIMIT:
        raise ExportError("Лимит выгрузок: не больше 2 в сутки на организацию.")


def consume_export_rate(db: Session, org_id: int) -> None:
    key = _rate_key(org_id)
    set_if_absent(db, key, window_start=_day_window_start(), count=0)
    n = incr_counter(db, key, window_start=_day_window_start(), by=1)
    if n > DAILY_LIMIT:
        raise ExportError("Лимит выгрузок: не больше 2 в сутки на организацию.")


def exports_dir(org_id: int) -> Path:
    d = org_files_root(org_id) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__str__") and type(obj).__name__ == "UUID":
        return str(obj)
    raise TypeError(f"Не сериализуется: {type(obj)}")


def _safe_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _write_json(zf: zipfile.ZipFile, arcname: str, data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
    zf.writestr(arcname, payload.encode("utf-8"))


def _write_xlsx(zf: zipfile.ZipFile, arcname: str, headers: list[str], rows: list[list[Any]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(headers)
    for row in rows:
        ws.append([_safe_cell(c) for c in row])
    buf = io.BytesIO()
    wb.save(buf)
    zf.writestr(arcname, buf.getvalue())


def _write_csv(zf: zipfile.ZipFile, arcname: str, headers: list[str], rows: list[list[Any]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow([_safe_cell(c) for c in row])
    zf.writestr(arcname, "\ufeff" + buf.getvalue())


def _strip_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_l = str(k).lower()
            if any(frag in key_l for frag in SECRET_KEY_FRAGMENTS):
                continue
            out[k] = _strip_secrets(v)
        return out
    if isinstance(obj, list):
        return [_strip_secrets(x) for x in obj]
    return obj


def _enum_val(v: Any) -> Any:
    return v.value if hasattr(v, "value") else v


def list_org_admins(db: Session, org_id: int) -> list[User]:
    users = list(
        db.scalars(
            select(User).where(
                User.org_id == org_id,
                User.role == UserRole.user,
                User.is_active.is_(True),
            )
        ).all()
    )
    admins: list[User] = []
    for u in users:
        role = u.org_role if u.org_role is not None else OrgRole.org_admin
        if role == OrgRole.org_admin:
            admins.append(u)
    return admins


def notify_export_created(
    *,
    org: Organization,
    initiator_email: str,
    admins: list[User],
) -> None:
    settings = get_settings()
    subject = f"[{settings.app_name}] Создана выгрузка данных организации"
    body = (
        f"Создана полная выгрузка данных организации «{org.name}».\n"
        f"Инициатор: {initiator_email}\n"
        f"Ссылка на скачивание действует 24 часа в кабинете (Настройки → Данные).\n"
        f"Если вы не запрашивали выгрузку — смените пароль и проверьте доступ сотрудников.\n"
    )
    for admin in admins:
        send_email(settings, to_addr=admin.email, subject=subject, body=body)


def build_org_export_zip(
    db: Session,
    org_id: int,
    *,
    user_id: int | None,
    progress_cb=None,
) -> dict[str, Any]:
    """Собрать ZIP dok-export-v1. Возвращает result для Job."""
    org = db.get(Organization, org_id)
    if org is None:
        raise ExportError("Организация не найдена")

    def prog(n: int) -> None:
        if progress_cb:
            progress_cb(n)

    prog(10)
    day = utcnow().date().isoformat()
    safe_org = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (org.name or "org"))[:40]
    filename = f"dok-export_{safe_org}_{day}.zip"
    out_path = exports_dir(org_id) / filename
    if out_path.exists():
        out_path.unlink()

    counterparties = list(db.scalars(select(Counterparty).where(Counterparty.org_id == org_id)).all())
    contracts = list(db.scalars(select(Contract).where(Contract.org_id == org_id)).all())
    documents = list(db.scalars(select(Document).where(Document.org_id == org_id)).all())
    users = list(db.scalars(select(User).where(User.org_id == org_id)).all())
    counters = list(db.scalars(select(Counter).where(Counter.org_id == org_id)).all())
    fields = list(db.scalars(select(OrgField).where(OrgField.org_id == org_id)).all())
    calendar = list(db.scalars(select(CalendarEvent).where(CalendarEvent.org_id == org_id)).all())
    payments = list(db.scalars(select(Payment).where(Payment.org_id == org_id)).all())
    events = list(
        db.scalars(select(Event).where(Event.org_id == org_id).order_by(Event.ts.asc())).all()
    )
    user_ids = [u.id for u in users]
    bookmarks = []
    notes = []
    if user_ids:
        bookmarks = list(
            db.scalars(select(LawBookmark).where(LawBookmark.user_id.in_(user_ids))).all()
        )
        notes = list(db.scalars(select(LawNote).where(LawNote.user_id.in_(user_ids))).all())

    prog(25)
    counts = {
        "users": len(users),
        "counterparties": len(counterparties),
        "contracts": len(contracts),
        "documents": len(documents),
        "counters": len(counters),
        "fields": len(fields),
        "calendar": len(calendar),
        "payments": len(payments),
        "events": len(events),
        "law_bookmarks": len(bookmarks),
        "law_notes": len(notes),
        "document_files": 0,
    }

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        readme = (
            f"{FORMAT_VERSION}\n"
            f"Выгрузка организации «{org.name}» (id={org_id})\n"
            f"Дата: {day}\n\n"
            "Содержимое:\n"
            "- organization.json — реквизиты (без секретов)\n"
            "- users.json / users.csv — сотрудники без паролей и 2FA\n"
            "- counterparties / contracts / documents — таблицы XLSX+JSON\n"
            "- files/documents/ — файлы DOCX/PDF\n"
            "- templates_org/, branding/, fields.json, numbering.json\n"
            "- calendar.json, law_bookmarks_notes.json, payments.xlsx, events.csv\n\n"
            "Импорт архива обратно в сервис пока не поддерживается.\n"
            "См. docs/формат_экспорта_v1.md в репозитории Док.Москва.\n"
        )
        zf.writestr("README.txt", readme.encode("utf-8"))

        _write_json(
            zf,
            "organization.json",
            {
                "id": org.id,
                "name": org.name,
                "created_at": org.created_at,
                "requisites": _strip_secrets(org.requisites or {}),
            },
        )

        users_json = [
            {
                "id": u.id,
                "email": u.email,
                "role": _enum_val(u.role),
                "org_role": _enum_val(u.org_role) if u.org_role else "org_admin",
                "is_active": u.is_active,
                "email_verified": u.email_verified,
                "created_at": u.created_at,
                "totp_enabled": bool(u.totp_enabled),
            }
            for u in users
        ]
        _write_json(zf, "users.json", users_json)
        _write_csv(
            zf,
            "users.csv",
            ["id", "email", "role", "org_role", "is_active", "email_verified", "created_at", "totp_enabled"],
            [
                [
                    u["id"],
                    u["email"],
                    u["role"],
                    u["org_role"],
                    u["is_active"],
                    u["email_verified"],
                    u["created_at"],
                    u["totp_enabled"],
                ]
                for u in users_json
            ],
        )

        prog(40)
        cp_headers = [
            "id",
            "type",
            "name",
            "fio",
            "inn",
            "kpp",
            "ogrn",
            "snils",
            "passport_series",
            "passport_number",
            "passport_issuer",
            "passport_date",
            "address",
            "phone",
            "email",
            "bank_name",
            "bank_bik",
            "bank_account",
            "bank_corr_account",
            "notes",
            "source",
        ]
        cp_rows = []
        cp_json = []
        for c in counterparties:
            row = {
                "id": c.id,
                "type": _enum_val(c.type),
                "name": c.name,
                "fio": c.fio,
                "inn": c.inn,
                "kpp": c.kpp,
                "ogrn": c.ogrn,
                "snils": c.snils,
                "passport_series": c.passport_series,
                "passport_number": c.passport_number,
                "passport_issuer": c.passport_issuer,
                "passport_date": c.passport_date,
                "address": c.address,
                "phone": c.phone,
                "email": c.email,
                "bank_name": c.bank_name,
                "bank_bik": c.bank_bik,
                "bank_account": c.bank_account,
                "bank_corr_account": c.bank_corr_account,
                "notes": c.notes,
                "source": _enum_val(c.source),
            }
            cp_json.append(row)
            cp_rows.append([row[h] for h in cp_headers])
        _write_json(zf, "counterparties.json", cp_json)
        _write_xlsx(zf, "counterparties.xlsx", cp_headers, cp_rows)

        ct_headers = [
            "id",
            "counterparty_id",
            "template",
            "number",
            "signed_on",
            "ends_on",
            "amount",
            "file_path",
        ]
        ct_json = []
        ct_rows = []
        for c in contracts:
            row = {
                "id": c.id,
                "counterparty_id": c.counterparty_id,
                "template": c.template,
                "number": c.number,
                "signed_on": c.signed_on,
                "ends_on": c.ends_on,
                "amount": str(c.amount) if c.amount is not None else None,
                "file_path": c.file_path,
            }
            ct_json.append(row)
            ct_rows.append([row[h] for h in ct_headers])
        _write_json(zf, "contracts.json", ct_json)
        _write_xlsx(zf, "contracts.xlsx", ct_headers, ct_rows)

        prog(55)
        doc_headers = [
            "id",
            "contract_id",
            "counterparty_id",
            "template",
            "number",
            "format",
            "file_path",
            "created_at",
            "created_by",
            "archive_path",
        ]
        doc_json = []
        doc_rows = []
        files_added = 0
        for d in documents:
            arc = f"files/documents/{d.id}_{Path(d.file_path or 'file').name}"
            row = {
                "id": d.id,
                "contract_id": d.contract_id,
                "counterparty_id": d.counterparty_id,
                "template": d.template,
                "number": d.number,
                "format": _enum_val(d.format),
                "file_path": d.file_path,
                "created_at": d.created_at,
                "created_by": d.created_by,
                "archive_path": arc,
                "context": d.context or {},
            }
            doc_json.append(row)
            doc_rows.append([row[h] for h in doc_headers])
            try:
                src = absolute_file(d)
                if src.is_file():
                    zf.write(src, arcname=arc)
                    files_added += 1
            except (FileNotFoundError, OSError):
                log.warning("document file missing org=%s doc=%s", org_id, d.id)
        counts["document_files"] = files_added
        _write_json(zf, "documents.json", doc_json)
        _write_xlsx(zf, "documents.xlsx", doc_headers, doc_rows)

        prog(70)
        _write_json(
            zf,
            "numbering.json",
            [
                {
                    "key": c.key,
                    "prefix": c.prefix,
                    "value": c.value,
                    "suffix": c.suffix,
                }
                for c in counters
            ],
        )
        _write_json(
            zf,
            "fields.json",
            [
                {
                    "name": f.name,
                    "label": f.label,
                    "field_type": _enum_val(f.field_type),
                    "required": f.required,
                    "default_value": f.default_value,
                    "hint": f.hint,
                    "options": f.options,
                }
                for f in fields
            ],
        )
        _write_json(
            zf,
            "calendar.json",
            [
                {
                    "id": e.id,
                    "title": e.title,
                    "notes": e.notes,
                    "due_on": e.due_on,
                    "kind": _enum_val(e.kind),
                    "status": _enum_val(e.status),
                    "remind_days_before": getattr(e, "remind_days_before", None),
                    "counterparty_id": getattr(e, "counterparty_id", None),
                    "document_id": getattr(e, "document_id", None),
                    "contract_id": getattr(e, "contract_id", None),
                }
                for e in calendar
            ],
        )
        _write_json(
            zf,
            "law_bookmarks_notes.json",
            {
                "bookmarks": [
                    {
                        "user_id": b.user_id,
                        "act_id": b.act_id,
                        "fragment_id": b.fragment_id,
                        "bookmark_key": b.bookmark_key,
                        "created_at": b.created_at,
                    }
                    for b in bookmarks
                ],
                "notes": [
                    {
                        "user_id": n.user_id,
                        "fragment_id": n.fragment_id,
                        "body": n.body,
                        "updated_at": n.updated_at,
                    }
                    for n in notes
                ],
            },
        )

        pay_headers = [
            "id",
            "amount_kop",
            "amount_base_kop",
            "discount_kop",
            "purpose",
            "status",
            "source",
            "created_at",
            "updated_at",
        ]
        pay_rows = []
        pay_json = []
        for p in payments:
            row = {
                "id": str(p.id),
                "amount_kop": p.amount_kop,
                "amount_base_kop": p.amount_base_kop,
                "discount_kop": p.discount_kop,
                "purpose": p.purpose,
                "status": _enum_val(p.status),
                "source": _enum_val(p.source),
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            pay_json.append(row)
            pay_rows.append([row[h] for h in pay_headers])
        _write_json(zf, "payments.json", pay_json)
        _write_xlsx(zf, "payments.xlsx", pay_headers, pay_rows)

        _write_csv(
            zf,
            "events.csv",
            ["id", "type", "user_id", "ts", "details_json"],
            [
                [
                    e.id,
                    e.type,
                    e.user_id,
                    e.ts,
                    json.dumps(_strip_secrets(e.details or {}), ensure_ascii=False, default=_json_default),
                ]
                for e in events
            ],
        )

        prog(85)
        # templates_org
        try:
            tdir = org_templates_dir(org_id)
            if tdir.is_dir():
                for path in tdir.rglob("*"):
                    if path.is_file():
                        rel = path.relative_to(tdir).as_posix()
                        zf.write(path, arcname=f"templates_org/{rel}")
        except FileNotFoundError:
            pass

        # branding
        try:
            bdir = branding_dir(org_id)
            if bdir.is_dir():
                for path in bdir.rglob("*"):
                    if path.is_file():
                        rel = path.relative_to(bdir).as_posix()
                        zf.write(path, arcname=f"branding/{rel}")
        except FileNotFoundError:
            pass

        _write_json(
            zf,
            "manifest.json",
            {
                "format": FORMAT_VERSION,
                "exported_at": utcnow().isoformat(),
                "org_id": org_id,
                "org_name": org.name,
                "counts": counts,
                "expires_hint_hours": TTL_HOURS,
            },
        )

    prog(95)
    settings = get_settings()
    rel = str(out_path.resolve().relative_to(Path(settings.files_root).resolve()))
    record_event(
        db,
        type="org.export.created",
        org_id=org_id,
        user_id=user_id,
        details={"filename": filename, "counts": counts},
        commit=False,
    )
    db.flush()
    return {
        "file_path": rel,
        "filename": filename,
        "download_url": None,  # заполняется с job.id
        "view_url": "/cabinet/settings/data?ok=exported",
        "counts": counts,
        "format": FORMAT_VERSION,
    }


def cleanup_stale_exports(*, older_than_hours: int = TTL_HOURS) -> int:
    root = Path(get_settings().files_root)
    if not root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    removed = 0
    for org_dir in root.iterdir():
        exports = org_dir / "exports"
        if not exports.is_dir():
            continue
        for path in exports.iterdir():
            if not path.is_file():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def export_stats(db: Session) -> dict[str, int]:
    n = int(
        db.scalar(
            select(func.count()).select_from(Event).where(Event.type == "org.export.created")
        )
        or 0
    )
    return {"exports_total": n}
