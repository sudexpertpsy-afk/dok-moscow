"""W-50.1 §4: manage.py mark_internal."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

from app.defaults import empty_requisites
from app.models import Organization
from manage import cmd_mark_internal, main


def test_mark_internal_idempotent_and_missing_warns(app):
    _client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        o1 = Organization(name="A", requisites=empty_requisites())
        o2 = Organization(name="B", requisites=empty_requisites())
        db.add_all([o1, o2])
        db.commit()
        id1, id2 = o1.id, o2.id
    finally:
        db.close()

    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cmd_mark_internal([id1, 99999, id2])
    assert rc == 0
    assert f"помечено: {id1}, {id2}" in out.getvalue()
    assert "99999" in err.getvalue()

    db = dbmod.SessionLocal()
    try:
        assert db.get(Organization, id1).is_internal is True
        assert db.get(Organization, id2).is_internal is True
    finally:
        db.close()

    # идемпотентно
    out2 = io.StringIO()
    with redirect_stdout(out2), redirect_stderr(io.StringIO()):
        assert cmd_mark_internal([id1, id2]) == 0
    assert f"помечено: {id1}, {id2}" in out2.getvalue()


def test_mark_internal_cli_env(app, monkeypatch):
    _client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Env", requisites=empty_requisites())
        db.add(org)
        db.commit()
        oid = org.id
    finally:
        db.close()

    monkeypatch.setenv("INTERNAL_ORG_IDS", str(oid))
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = main(["mark_internal"])
    assert rc == 0
    assert f"помечено: {oid}" in out.getvalue()
