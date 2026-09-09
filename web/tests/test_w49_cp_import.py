"""W-49 A: импорт контрагентов CSV/XLSX/journal."""

from __future__ import annotations

import json
from io import BytesIO

from datetime import timedelta

from openpyxl import Workbook
from sqlalchemy import select
import pytest

from app.defaults import empty_requisites
from app.models import (
    Counterparty,
    CounterpartyType,
    Event,
    Organization,
    Subscription,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from app.services.cp_import import (
    DuplicateMode,
    ImportRejectedXls,
    auto_map_columns,
    classify_rows,
    commit_rows,
    decode_bytes,
    parse_csv_bytes,
    parse_journal_bytes,
    parse_upload,
    parse_xlsx_bytes,
)
from conftest import csrf_from, login


def _seed(dbmod, email="imp@example.com", *, tariff: TariffCode = TariffCode.specialist):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="ImportOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        ensure_beta_subscriptions(db)
        t = db.scalar(select(Tariff).where(Tariff.code == tariff))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        if sub and t:
            sub.tariff_id = t.id
            sub.status = SubscriptionStatus.active
            sub.ends_at = utcnow() + timedelta(days=30)
            sub.is_beta = False
        db.commit()
        return org.id, email
    finally:
        db.close()


def test_decode_cp1251_and_utf8_bom():
    raw = "Наименование;ИНН\nООО А;7707083893\n".encode("cp1251")
    assert "ООО А" in decode_bytes(raw)
    bom = "name;inn\nx;1\n".encode("utf-8-sig")
    assert decode_bytes(bom).startswith("name")
    assert not decode_bytes(bom).startswith("\ufeff")


def test_csv_delimiters_and_auto_map():
    table = parse_csv_bytes("ИНН,Название,Тип\n7707083893,ООО Ромашка,ЮЛ\n".encode("utf-8"))
    assert table.headers[0] == "ИНН"
    mapping = auto_map_columns(table.headers)
    assert mapping["inn"] == 0
    assert mapping["name"] == 1
    assert mapping["type"] == 2

    table2 = parse_csv_bytes("ФИО\tИНН\nИванов Иван Иванович\t500100732259\n".encode("utf-8"))
    assert len(table2.rows) == 1


def test_xlsx_and_xls_reject():
    wb = Workbook()
    ws = wb.active
    ws.append(["Тип", "Наименование", "ИНН"])
    ws.append(["ЮЛ", "ООО Тест", "7707083893"])
    buf = BytesIO()
    wb.save(buf)
    table = parse_xlsx_bytes(buf.getvalue())
    assert table.rows[0][1] == "ООО Тест"
    with pytest.raises(ImportRejectedXls) as ei:
        parse_upload("old.xls", b"\xd0\xcf\x11\xe0")
    msg = str(ei.value).lower()
    assert "xls" in msg and "xlsx" in msg
    assert "сохранить" in msg


def test_xls_reject_http_event(app):
    client, dbmod = app
    org_id, email = _seed(dbmod, "xls@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303
    csrf = csrf_from(client, "/cabinet/counterparties/import")
    r = client.post(
        "/cabinet/counterparties/import/upload",
        data={"csrf_token": csrf},
        files={"file": ("legacy.xls", b"\xd0\xcf\x11\xe0\xa1\xb1", "application/vnd.ms-excel")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    db = dbmod.SessionLocal()
    try:
        ev = db.scalars(
            select(Event)
            .where(Event.org_id == org_id, Event.type == "import.rejected_xls")
            .order_by(Event.id.desc())
        ).first()
        assert ev is not None
        assert (ev.details or {}).get("filename") == "legacy.xls"
    finally:
        db.close()

def test_validators_and_duplicates(app):
    _, dbmod = app
    org_id, _ = _seed(dbmod, "dup@example.com")
    db = dbmod.SessionLocal()
    try:
        table = parse_csv_bytes(
            (
                "Тип;Наименование;ИНН;КПП\n"
                "ЮЛ;ООО Один;7707083893;770701001\n"
                "ЮЛ;ООО Дубль;7707083893;770701001\n"
                "ЮЛ;;123\n"
            ).encode("utf-8")
        )
        mapping = auto_map_columns(table.headers)
        existing = list(db.scalars(select(Counterparty).where(Counterparty.org_id == org_id)))
        rows = classify_rows(table, mapping, default_type=None, existing=existing)
        # дубль внутри файла схлопнут
        assert len(rows) == 2
        assert any(r.status.value == "error" for r in rows)

        # импорт валидной
        ok_rows = [r for r in rows if r.status.value != "error"]
        res = commit_rows(
            db,
            org_id,
            ok_rows,
            mode=DuplicateMode.fill,
            require_zero_errors=False,
            user_id=None,
            filename="t.csv",
            token=None,
        )
        db.commit()
        assert res.created == 1

        # повтор — skip
        existing = list(db.scalars(select(Counterparty).where(Counterparty.org_id == org_id)))
        rows2 = classify_rows(table, mapping, default_type=None, existing=existing)
        ok2 = [r for r in rows2 if r.status.value != "error"]
        res2 = commit_rows(
            db,
            org_id,
            ok2,
            mode=DuplicateMode.skip,
            require_zero_errors=False,
            user_id=None,
            filename="t.csv",
        )
        db.commit()
        assert res2.skipped >= 1
        assert db.scalar(select(Counterparty).where(Counterparty.org_id == org_id)).name == "ООО Один"
    finally:
        db.close()


def test_journal_jsonl_extract():
    lines = [
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00",
                "template": "x.docx",
                "output": "y.docx",
                "context": {
                    "название_заказчика": "ООО ИзЖурнала",
                    "инн_заказчика": "7707083893",
                },
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "ts": "2026-01-02T00:00:00",
                "template": "z.docx",
                "output": "w.docx",
                "context": {"фио_клиента": "Петров Пётр", "паспорт_серия": "4500", "паспорт_номер": "123456"},
            },
            ensure_ascii=False,
        ),
    ]
    table = parse_journal_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    assert len(table.rows) == 2
    assert "ООО ИзЖурнала" in table.rows[0]


def test_wizard_http_and_idor(app):
    client, dbmod = app
    org_a, email_a = _seed(dbmod, "a-imp@example.com")
    org_b, email_b = _seed(dbmod, "b-imp@example.com")
    assert login(client, email_a, "Passw0rd!").status_code == 303

    r = client.get("/cabinet/counterparties/")
    assert "Импорт" in r.text
    r = client.get("/cabinet/counterparties/import")
    assert r.status_code == 200
    assert "Шаблон" in r.text or "шаблон" in r.text.lower()

    tpl = client.get("/cabinet/counterparties/import/template.xlsx")
    assert tpl.status_code == 200
    assert tpl.content[:2] == b"PK"

    csrf = csrf_from(client, "/cabinet/counterparties/import")
    csv_body = "Тип;Наименование;ИНН\nЮЛ;ООО ИмпортТест;7707083893\n".encode("utf-8")
    r = client.post(
        "/cabinet/counterparties/import/upload",
        data={"csrf_token": csrf},
        files={"file": ("cp.csv", csv_body, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/import/map" in r.headers["location"]

    r = client.get("/cabinet/counterparties/import/map")
    assert r.status_code == 200
    assert "Наименование" in r.text

    csrf = csrf_from(client, "/cabinet/counterparties/import/map")
    # mapping fields from auto — leave defaults via empty post of selects from page is hard;
    # post skip-all then set via service path already covered — here submit auto-selected options.
    # Minimal: re-save map with auto indices from headers Типы.
    from app.services.cp_import import auto_map_columns, load_table_snapshot

    token = client.cookies.get("session")  # starlette session in cookie jar
    # get token from session by loading via second request flow — use map form fields
    data = {"csrf_token": csrf, "default_type": "auto"}
    for key, idx in auto_map_columns(["Тип", "Наименование", "ИНН"]).items():
        data[f"map_{key}"] = "" if idx is None else str(idx)
    r = client.post(
        "/cabinet/counterparties/import/map",
        data=data,
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = client.get("/cabinet/counterparties/import/preview")
    assert r.status_code == 200
    assert "Готово" in r.text or "готово" in r.text.lower()

    csrf = csrf_from(client, "/cabinet/counterparties/import/preview")
    r = client.post(
        "/cabinet/counterparties/import/run",
        data={
            "csrf_token": csrf,
            "duplicate_mode": "fill",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/import/done" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        cp = db.scalar(
            select(Counterparty).where(
                Counterparty.org_id == org_a, Counterparty.inn == "7707083893"
            )
        )
        assert cp is not None
        assert cp.name == "ООО ИмпортТест"
        # token dir exists under org_a
        # IDOR: org_b cannot resolve org_a token path via API report without session result
    finally:
        db.close()

    # logout, login as B, try report without own session — 404
    csrf = csrf_from(client, "/cabinet/counterparties/")
    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert login(client, email_b, "Passw0rd!").status_code == 303
    assert client.get("/cabinet/counterparties/import/report").status_code == 404

    from app.services.cp_import import ImportErrorMsg, load_table_snapshot, new_token

    try:
        load_table_snapshot(org_b, new_token())
        raise AssertionError("expected missing for other org")
    except ImportErrorMsg:
        pass
def test_async_job_import(app, monkeypatch):
    import app.services.cp_import as cp_mod
    import app.routers.cp_import as cp_router

    monkeypatch.setattr(cp_mod, "SYNC_MAX_ROWS", 3)
    monkeypatch.setattr(cp_router, "SYNC_MAX_ROWS", 3)

    client, dbmod = app
    org_id, email = _seed(dbmod, "job-imp@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303

    lines = ["Тип;ФИО"]
    for i in range(5):
        lines.append(f"ФЛ;Тестов Тест {i:03d}")
    body = ("\n".join(lines) + "\n").encode("utf-8")

    csrf = csrf_from(client, "/cabinet/counterparties/import")
    r = client.post(
        "/cabinet/counterparties/import/upload",
        data={"csrf_token": csrf},
        files={"file": ("many.csv", body, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    csrf = csrf_from(client, "/cabinet/counterparties/import/map")
    data = {"csrf_token": csrf, "default_type": "fl"}
    mapping = auto_map_columns(["Тип", "ФИО"])
    for key, idx in mapping.items():
        data[f"map_{key}"] = "" if idx is None else str(idx)
    client.post("/cabinet/counterparties/import/map", data=data, follow_redirects=False)

    csrf = csrf_from(client, "/cabinet/counterparties/import/preview")
    r = client.post(
        "/cabinet/counterparties/import/run",
        data={"csrf_token": csrf, "duplicate_mode": "fill"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/cabinet/jobs/" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        count = len(list(db.scalars(select(Counterparty).where(Counterparty.org_id == org_id))))
        assert count == 5
    finally:
        db.close()


def test_guest_row_limit(app):
    client, dbmod = app
    _seed(dbmod, "guest-imp@example.com", tariff=TariffCode.guest)
    assert login(client, "guest-imp@example.com", "Passw0rd!").status_code == 303
    lines = ["Тип;ФИО"] + [f"ФЛ;Гость {i}" for i in range(51)]
    body = ("\n".join(lines) + "\n").encode("utf-8")
    csrf = csrf_from(client, "/cabinet/counterparties/import")
    r = client.post(
        "/cabinet/counterparties/import/upload",
        data={"csrf_token": csrf},
        files={"file": ("big.csv", body, "text/csv")},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "лимит" in r.text.lower() or "50" in r.text