"""W-46E: мастер комплекта в БД и rate_counters."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from app.models import WizardSession, utcnow
from app.services.package_wizard_store import (
    clear_wizard,
    load_wizard,
    purge_expired_wizard_sessions,
    save_wizard,
)
from app.services.rate_counters import get_count, incr_counter


def _req(sid="a" * 32, org_id=None, user_id=None):
    req = MagicMock()
    req.session = {"sid": sid, "org_id": org_id, "user_id": user_id}
    return req


def test_wizard_roundtrip_db(app):
    _client, _dbmod = app
    req = _req()
    assert load_wizard(req) == {}
    save_wizard(req, {"тип": "Юрлицо", "selected": ["a.docx"]})
    data = load_wizard(req)
    assert data["тип"] == "Юрлицо"
    assert data["selected"] == ["a.docx"]
    clear_wizard(req)
    assert load_wizard(req) == {}


def test_wizard_shared_across_calls(app):
    """Два вызова SessionLocal видят одну запись."""
    _client, _dbmod = app
    req = _req(sid="b" * 32)
    save_wizard(req, {"step": 1})
    req2 = _req(sid="b" * 32)
    assert load_wizard(req2)["step"] == 1
    save_wizard(req2, {"step": 2})
    assert load_wizard(req)["step"] == 2


def test_purge_expired_wizard(app):
    _client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        past = utcnow() - timedelta(hours=48)
        db.add(
            WizardSession(
                sid="c" * 32,
                state={"x": 1},
                updated_at=past,
                expires_at=past,
            )
        )
        db.commit()
        n = purge_expired_wizard_sessions(db)
        db.commit()
        assert n >= 1
        assert db.get(WizardSession, "c" * 32) is None
    finally:
        db.close()


def test_rate_counter_incr(app):
    _client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        key = "test:counter:1"
        w = utcnow()
        assert incr_counter(db, key, window_start=w, by=1) == 1
        assert incr_counter(db, key, window_start=w, by=2) == 3
        db.commit()
        assert get_count(db, key) == 3
    finally:
        db.close()
