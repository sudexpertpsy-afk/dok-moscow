"""Календарь, планирование и напоминания кабинета."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    CalendarEvent,
    CalendarEventKind,
    CalendarEventStatus,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.calendar_reminders import process_calendar_reminders
from app.services.calendar_svc import (
    create_event,
    due_reminders,
    month_grid,
    parse_ru_date,
    upsert_contract_from_package,
)
from conftest import csrf_from, login


def _seed(dbmod, email="cal@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="КалендарьОрг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add(user)
        db.commit()
        return org.id, user.id
    finally:
        db.close()


def test_parse_ru_date():
    assert parse_ru_date("01.08.2026") == date(2026, 8, 1)
    assert parse_ru_date("2026-08-01") == date(2026, 8, 1)
    assert parse_ru_date("") is None


def test_calendar_page_and_create(app):
    client, dbmod = app
    _seed(dbmod)
    assert login(client, "cal@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/calendar/")
    assert r.status_code == 200
    assert "Календарь" in r.text
    assert "Требуют внимания" in r.text

    token = csrf_from(client, "/cabinet/calendar/new")
    due = (date.today() + timedelta(days=5)).isoformat()
    r = client.post(
        "/cabinet/calendar/new",
        data={
            "csrf_token": token,
            "title": "Встреча с заказчиком",
            "due_on": due,
            "kind": "meeting",
            "notes": "обсудить акт",
            "remind_days_before": "3",
            "counterparty_id": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        ev = db.scalar(select(CalendarEvent).where(CalendarEvent.title == "Встреча с заказчиком"))
        assert ev is not None
        assert ev.kind == CalendarEventKind.meeting
        assert ev.remind_days_before == 3
    finally:
        db.close()

    r = client.get("/cabinet/calendar/")
    assert "Встреча с заказчиком" in r.text


def test_package_creates_contract_calendar_event(app):
    _, dbmod = app
    org_id, user_id = _seed(dbmod, "cal2@example.com")
    db = dbmod.SessionLocal()
    try:
        from app.models import Counterparty, CounterpartySource, CounterpartyType

        org = db.get(Organization, org_id)
        cp = Counterparty(
            org_id=org_id,
            type=CounterpartyType.ul,
            name="ООО Заказчик",
            source=CounterpartySource.manual,
        )
        db.add(cp)
        db.flush()
        ends = date.today() + timedelta(days=30)
        contract = upsert_contract_from_package(
            db,
            org=org,
            counterparty=cp,
            contract_template="Договор_рецензия_юрлицо.docx",
            context={
                "номер_договора": "Д-100",
                "дата_договора": date.today().strftime("%d.%m.%Y"),
                "дата_окончания": ends.strftime("%d.%m.%Y"),
                "сумма": "15000",
            },
            document_ids=[],
            user_id=user_id,
        )
        db.commit()
        assert contract is not None
        assert contract.ends_on == ends
        evs = db.scalars(
            select(CalendarEvent).where(CalendarEvent.contract_id == contract.id)
        ).all()
        kinds = {e.kind for e in evs}
        assert CalendarEventKind.contract_end in kinds
        assert CalendarEventKind.contract_start in kinds
        end_ev = next(e for e in evs if e.kind == CalendarEventKind.contract_end)
        assert end_ev.remind_days_before == 7
    finally:
        db.close()


def test_month_grid_and_reminders(app):
    _, dbmod = app
    org_id, user_id = _seed(dbmod, "cal3@example.com")
    db = dbmod.SessionLocal()
    try:
        due = date.today() + timedelta(days=1)
        create_event(
            db,
            org_id=org_id,
            title="Напомнить завтра",
            due_on=due,
            kind=CalendarEventKind.plan,
            remind_days_before=1,
            created_by=user_id,
        )
        db.commit()
        today = date.today()
        weeks = month_grid(db, org_id, today.year, today.month)
        flat = [ev for week in weeks for cell in week for ev in cell.events]
        assert any(e.title == "Напомнить завтра" for e in flat)
        ready = due_reminders(db, today=today)
        assert any(e.title == "Напомнить завтра" for e in ready)
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "app.services.calendar_reminders.notify_calendar_reminder", return_value=True
        ):
            n = process_calendar_reminders(db)
        assert n >= 1
        ev = db.scalar(select(CalendarEvent).where(CalendarEvent.title == "Напомнить завтра"))
        assert ev.reminded_at is not None
        assert process_calendar_reminders(db) == 0
    finally:
        db.close()


def test_mark_done(app):
    client, dbmod = app
    org_id, user_id = _seed(dbmod, "cal4@example.com")
    db = dbmod.SessionLocal()
    try:
        ev = create_event(
            db,
            org_id=org_id,
            title="Закрыть",
            due_on=date.today(),
            created_by=user_id,
        )
        db.commit()
        eid = ev.id
    finally:
        db.close()
    assert login(client, "cal4@example.com", "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/calendar/")
    r = client.post(
        f"/cabinet/calendar/{eid}/done",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        assert db.get(CalendarEvent, eid).status == CalendarEventStatus.done
    finally:
        db.close()
