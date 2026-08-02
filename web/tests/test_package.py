"""W-04: мастер комплектов ФЛ / ЮЛ / ГПД."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from app.defaults import empty_requisites
from app.models import Counterparty, Document, Organization, User, UserRole
from app.security import hash_password
from app.services.package_master import extra_options, selected_templates
from conftest import csrf_from, login

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))
sys.path.insert(0, str(REPO / "core" / "tests"))
from fixtures_sample_context import build_context_for_template  # noqa: E402


def _seed(dbmod, email="pkg@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ПакетОрг", requisites=empty_requisites())
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
        return org.id
    finally:
        db.close()


def _run_wizard(client, тип, contract, core_overrides=None, extras_force=None):
    assert login(client, "pkg@example.com", "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/package/?тип={тип}")
    assert r.status_code == 200

    # step1
    csrf = csrf_from(client, f"/cabinet/package/?тип={тип}")
    ctx = build_context_for_template(contract)
    if core_overrides:
        ctx.update(core_overrides)
    from docfiller_core.master import core_fields

    data = {"csrf_token": csrf, "тип": тип, "counterparty_id": "new"}
    for f in core_fields(тип):
        data[f] = str(ctx.get(f, ""))
        if f == "номер_договора":
            data[f] = ""  # auto
    r = client.post("/cabinet/package/step1", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:400]

    # step2: чекбоксы сопутствующих не растянуты на 100% (layout)
    step2 = client.get("/cabinet/package/step2")
    assert step2.status_code == 200
    assert "Сопутствующие" in step2.text or "Договор" in step2.text
    if "Сопутствующие" in step2.text:
        assert 'class="check-row"' in step2.text
        assert 'type="checkbox"' in step2.text

    csrf = csrf_from(client, "/cabinet/package/step2")
    extras = extra_options(тип, contract, empty_requisites())
    data = {"csrf_token": csrf, "contract_template": contract, "action": "next"}
    for name, on in extras:
        if extras_force is not None:
            if name in extras_force:
                data[f"extra_{name}"] = "1"
        elif on:
            data[f"extra_{name}"] = "1"
    r = client.post("/cabinet/package/step2", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:400]

    # step3
    csrf = csrf_from(client, "/cabinet/package/step3")
    selected = selected_templates(
        contract,
        extras_force
        if extras_force is not None
        else [n for n, on in extras if on],
    )
    from docfiller_core.master import collect_variables
    from app.services.templates import templates_dir

    core_names, additional = collect_variables(templates_dir(), selected, тип)
    data = {"csrf_token": csrf}
    for f in core_names + additional:
        if f == "номер_договора" or f.startswith("номер_"):
            data[f] = ""
        else:
            data[f] = str(ctx.get(f, ""))
    r = client.post("/cabinet/package/step3", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:800]
    assert r.headers["location"] == "/cabinet/package/done"

    r = client.get("/cabinet/package/done")
    assert r.status_code == 200
    assert "Комплект готов" in r.text
    return r


def test_fl_package_contract_bill_act_pko(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))
    _seed(dbmod)

    r = _run_wizard(
        client,
        "Физлицо",
        "Договор_услуги_v2.docx",
        extras_force=[
            "Счёт_на_оплату.docx",
            "Акт_оказанных_услуг.docx",
            "ПКО_КО-1.docx",
        ],
    )
    assert "Счёт" in r.text or "счёт" in r.text.lower() or "на оплату" in r.text.lower() or "Документ" in r.text

    db = dbmod.SessionLocal()
    try:
        docs = db.query(Document).all() if hasattr(db, "query") else list(
            __import__("sqlalchemy").orm.Session.scalars(
                db, __import__("sqlalchemy").select(Document)
            )
        )
        from sqlalchemy import select

        docs = list(db.scalars(select(Document)).all())
        names = {d.template for d in docs}
        assert "Договор_услуги_v2.docx" in names
        assert "Счёт_на_оплату.docx" in names
        assert "Акт_оказанных_услуг.docx" in names
        assert "ПКО_КО-1.docx" in names
        assert len(docs) == 4
        assert all(d.counterparty_id for d in docs)
        cps = list(db.scalars(select(Counterparty)).all())
        assert len(cps) == 1
        assert cps[0].fio
    finally:
        db.close()

    # ZIP через фоновую задачу (W-30)
    r = client.get("/cabinet/package/zip", follow_redirects=False)
    assert r.status_code == 303
    assert "/cabinet/jobs/" in r.headers["location"]
    job_url = r.headers["location"]
    r = client.get(job_url.rstrip("/") + "/download", follow_redirects=False)
    assert r.status_code == 200
    zpath = tmp_path / "out.zip"
    zpath.write_bytes(r.content)
    with zipfile.ZipFile(zpath) as zf:
        assert len(zf.namelist()) == 4
    get_settings.cache_clear()


def test_ul_package(app, tmp_path):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))
    _seed(dbmod, email="ul@example.com")
    # login as ul user - need to change _run_wizard email; seed different
    # Re-seed login: override by logging in ul@
    def run():
        assert login(client, "ul@example.com", "Passw0rd!").status_code == 303
        contract = "Договор_услуги_юрлицо.docx"
        csrf = csrf_from(client, "/cabinet/package/?тип=Юрлицо")
        ctx = build_context_for_template(contract)
        from docfiller_core.master import core_fields

        data = {"csrf_token": csrf, "тип": "Юрлицо", "counterparty_id": "new"}
        for f in core_fields("Юрлицо"):
            data[f] = str(ctx.get(f, ""))
        r = client.post("/cabinet/package/step1", data=data, follow_redirects=False)
        assert r.status_code == 303
        csrf = csrf_from(client, "/cabinet/package/step2")
        extras = ["Счёт_на_оплату_юрлицо.docx", "Акт_оказанных_услуг_юрлицо.docx"]
        data = {"csrf_token": csrf, "contract_template": contract, "action": "next"}
        for n in extras:
            data[f"extra_{n}"] = "1"
        r = client.post("/cabinet/package/step2", data=data, follow_redirects=False)
        assert r.status_code == 303
        csrf = csrf_from(client, "/cabinet/package/step3")
        selected = selected_templates(contract, extras)
        from docfiller_core.master import collect_variables
        from app.services.templates import templates_dir

        core_names, additional = collect_variables(templates_dir(), selected, "Юрлицо")
        data = {"csrf_token": csrf}
        for f in core_names + additional:
            data[f] = "" if f.startswith("номер_") else str(ctx.get(f, ""))
        r = client.post("/cabinet/package/step3", data=data, follow_redirects=False)
        assert r.status_code == 303
        from sqlalchemy import select

        db = dbmod.SessionLocal()
        try:
            names = {d.template for d in db.scalars(select(Document)).all()}
            assert "Договор_услуги_юрлицо.docx" in names
            assert "Счёт_на_оплату_юрлицо.docx" in names
            assert "Акт_оказанных_услуг_юрлицо.docx" in names
        finally:
            db.close()

    run()
    get_settings.cache_clear()


def test_gpd_package(app, tmp_path):
    client, dbmod = app
    from app.config import get_settings
    from sqlalchemy import select

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))
    _seed(dbmod, email="gpd@example.com")
    assert login(client, "gpd@example.com", "Passw0rd!").status_code == 303
    contract = "Договор_ГПД_эксперт.docx"
    csrf = csrf_from(client, "/cabinet/package/?тип=Эксперт%20(ГПД)")
    ctx = build_context_for_template(contract)
    from docfiller_core.master import core_fields

    data = {"csrf_token": csrf, "тип": "Эксперт (ГПД)", "counterparty_id": "new"}
    for f in core_fields("Эксперт (ГПД)"):
        data[f] = str(ctx.get(f, ""))
    assert client.post("/cabinet/package/step1", data=data, follow_redirects=False).status_code == 303
    csrf = csrf_from(client, "/cabinet/package/step2")
    extras = ["Акт_ГПД_эксперт.docx", "Согласие_ПДн.docx"]
    data = {"csrf_token": csrf, "contract_template": contract, "action": "next"}
    for n in extras:
        data[f"extra_{n}"] = "1"
    assert client.post("/cabinet/package/step2", data=data, follow_redirects=False).status_code == 303
    csrf = csrf_from(client, "/cabinet/package/step3")
    selected = selected_templates(contract, extras)
    from docfiller_core.master import collect_variables
    from app.services.templates import templates_dir

    core_names, additional = collect_variables(templates_dir(), selected, "Эксперт (ГПД)")
    data = {"csrf_token": csrf}
    for f in core_names + additional:
        data[f] = "" if "номер" in f else str(ctx.get(f, ""))
    r = client.post("/cabinet/package/step3", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    db = dbmod.SessionLocal()
    try:
        names = {d.template for d in db.scalars(select(Document)).all()}
        assert contract in names
        assert "Акт_ГПД_эксперт.docx" in names
        assert "Согласие_ПДн.docx" in names
        doc = db.scalars(select(Document).where(Document.template == contract)).first()
    finally:
        db.close()

    # повтор
    r = client.get(f"/cabinet/package/repeat/{doc.id}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/package/step3"
    r = client.get("/cabinet/package/step3")
    assert r.status_code == 200
    assert ctx.get("фио_эксперта", "x")[:5] in r.text or "фио_эксперта" in r.text
    get_settings.cache_clear()
