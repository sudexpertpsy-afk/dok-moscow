"""W-43: факсимиле печати и подписей."""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Document,
    DocumentFormat,
    Organization,
    OrgRole,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.billing import ensure_tariffs
from app.services.branding import (
    SLOT_DIRECTOR,
    SLOT_PECHAT,
    VerdictLevel,
    evaluate_image,
    process_and_save,
    slot_path,
)
from app.services.facsimile import FacsimilePolicy, facsimile_policy
from app.services.org_fields import OrgFieldError, validate_field_name
from app.services.templates import ensure_core_on_path
from conftest import csrf_from, login


def _png_bytes(w: int, h: int, *, color=(20, 40, 180), bg=(255, 255, 255)) -> bytes:
    img = Image.new("RGB", (w, h), bg)
    for x in range(w // 8, w - w // 8):
        for y in range(h // 3, 2 * h // 3):
            if (x + y) % 3 == 0:
                img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _seed_org(dbmod, *, email: str = "fax@example.com") -> tuple[int, str]:
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="Fax Org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            org_role=OrgRole.org_admin,
            is_active=True,
        )
        db.add(user)
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.organization))
        assert tariff is not None
        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=tariff.id,
                period=SubscriptionPeriod.month,
                starts_at=utcnow() - timedelta(days=1),
                ends_at=utcnow() + timedelta(days=30),
                status=SubscriptionStatus.active,
                auto_renew=False,
                is_beta=False,
            )
        )
        db.commit()
        return org.id, email
    finally:
        db.close()


def test_px_mm_and_verdicts():
    from app.services.branding import px_to_mm

    assert abs(px_to_mm(472) - 40.0) < 0.2
    assert evaluate_image(_png_bytes(600, 600), kind="stamp").level == VerdictLevel.excellent
    assert evaluate_image(_png_bytes(460, 460), kind="stamp").level == VerdictLevel.usable
    assert evaluate_image(_png_bytes(100, 100), kind="stamp").level == VerdictLevel.rejected
    assert evaluate_image(_png_bytes(770, 260), kind="signature").level == VerdictLevel.excellent
    assert evaluate_image(_png_bytes(100, 50), kind="signature").level == VerdictLevel.rejected


def test_polyglot_rejected(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services.branding import BrandingError

    get_settings.cache_clear()
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        try:
            process_and_save(99, SLOT_PECHAT, b"%PDF-1.4 fake polyglot")
            raise AssertionError("expected BrandingError")
        except BrandingError:
            pass
    finally:
        get_settings.cache_clear()


def test_facsimile_policy_matrix():
    assert facsimile_policy("Счёт_на_оплату.docx") == FacsimilePolicy.allowed
    assert facsimile_policy("Сопроводительное_письмо.docx") == FacsimilePolicy.allowed
    assert facsimile_policy("Договор_услуги_v2.docx") == FacsimilePolicy.warn
    assert facsimile_policy("Акт_оказанных_услуг.docx") == FacsimilePolicy.warn
    assert facsimile_policy("ПКО_КО-1.docx") == FacsimilePolicy.forbidden
    assert facsimile_policy("Заключение_эксперта_гражданский_процесс.docx") == FacsimilePolicy.forbidden
    assert facsimile_policy("Согласие_ПДн.docx") == FacsimilePolicy.forbidden
    assert facsimile_policy("Ходатайство_о_назначении_экспертизы.docx") == FacsimilePolicy.forbidden


def test_reserved_facsimile_names_w41():
    for name in (
        "факсимиле_печать",
        "факсимиле_директор",
        "факсимиле_бухгалтер",
        "факсимиле_кассир",
        "признают_факсимиле",
    ):
        try:
            validate_field_name(name)
            raise AssertionError(name)
        except OrgFieldError:
            pass


def test_templates_have_placeholders_allowed_not_forbidden():
    root = Path("/workspace/core/Шаблоны")
    for name in ("Счёт_на_оплату.docx", "Сопроводительное_письмо.docx"):
        xml = ZipFile(root / name).read("word/document.xml").decode("utf-8", errors="ignore")
        assert "факсимиле_директор" in xml, name
        assert "факсимиле_печать" in xml, name
    for name in ("ПКО_КО-1.docx", "Согласие_ПДн.docx"):
        xml = ZipFile(root / name).read("word/document.xml").decode("utf-8", errors="ignore")
        assert "факсимиле_директор" not in xml, name
        assert "факсимиле_печать" not in xml, name


def test_branding_isolation_and_settings_ui(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        oid1, email1 = _seed_org(dbmod, email="fax1@example.com")
        oid2, _ = _seed_org(dbmod, email="fax2@example.com")
        process_and_save(oid1, SLOT_PECHAT, _png_bytes(600, 600))
        process_and_save(oid2, SLOT_PECHAT, _png_bytes(620, 620, color=(200, 0, 0)))
        assert slot_path(oid1, SLOT_PECHAT).read_bytes() != slot_path(oid2, SLOT_PECHAT).read_bytes()

        assert login(client, email1, "Passw0rd!").status_code == 303
        r = client.get("/cabinet/settings/branding")
        assert r.status_code == 200
        assert "Печать организации" in r.text
        r = client.get("/cabinet/settings/branding/печать/preview.png")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")
    finally:
        get_settings.cache_clear()


def test_generate_clean_docx_and_ui_rules(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        oid, email = _seed_org(dbmod, email="faxgen@example.com")
        process_and_save(oid, SLOT_PECHAT, _png_bytes(600, 600))
        process_and_save(oid, SLOT_DIRECTOR, _png_bytes(770, 260))
        assert login(client, email, "Passw0rd!").status_code == 303

        r = client.get("/cabinet/documents/new/ПКО_КО-1.docx")
        assert r.status_code == 200
        assert "Печать и подпись в PDF" not in r.text

        r = client.get("/cabinet/documents/new/Счёт_на_оплату.docx")
        assert r.status_code == 200
        assert "Печать и подпись в PDF" in r.text

        r = client.get("/cabinet/documents/new/Договор_услуги_v2.docx")
        assert r.status_code == 200
        assert "соглашении сторон" in r.text.lower() or "факсимиле на договоре" in r.text.lower()

        # генерация письма (без банковского гейта счёта)
        token = csrf_from(client, "/cabinet/documents/new/Сопроводительное_письмо.docx")
        from app.services.templates import template_variables

        db = dbmod.SessionLocal()
        try:
            org = db.get(Organization, oid)
            vars_ = template_variables("Сопроводительное_письмо.docx", org.requisites, org_id=oid)
        finally:
            db.close()
        data = {
            "csrf_token": token,
            "facsimile_pdf": "1",
        }
        for v in vars_:
            data.setdefault(v, "тест")
        r = client.post(
            "/cabinet/documents/new/Сопроводительное_письмо.docx",
            data=data,
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text[:500]
        db = dbmod.SessionLocal()
        try:
            doc = db.scalar(select(Document).order_by(Document.id.desc()))
            assert doc is not None
            assert doc.format == DocumentFormat.docx
            assert doc.context.get("_facsimile_pdf") is True
            assert doc.context.get("_facsimile_docx") is False
            path = Path(tmp_path) / doc.file_path
            assert path.is_file()
        finally:
            db.close()

        # запрещённый шаблон: POST с флагами не включает факсимиле
        token = csrf_from(client, "/cabinet/documents/new/ПКО_КО-1.docx")
        db = dbmod.SessionLocal()
        try:
            org = db.get(Organization, oid)
            vars_ = template_variables("ПКО_КО-1.docx", org.requisites, org_id=oid)
        finally:
            db.close()
        data = {"csrf_token": token, "facsimile_pdf": "1", "facsimile_docx": "1"}
        for v in vars_:
            data.setdefault(v, "тест")
        r = client.post("/cabinet/documents/new/ПКО_КО-1.docx", data=data, follow_redirects=False)
        if r.status_code == 303:
            db = dbmod.SessionLocal()
            try:
                doc = db.scalar(select(Document).order_by(Document.id.desc()))
                assert doc.context.get("_facsimile_pdf") is False
                assert doc.context.get("_facsimile_docx") is False
            finally:
                db.close()
    finally:
        get_settings.cache_clear()


def test_fill_with_images_inserts_media(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        oid, _ = _seed_org(dbmod, email="faxfill@example.com")
        process_and_save(oid, SLOT_PECHAT, _png_bytes(600, 600))
        process_and_save(oid, SLOT_DIRECTOR, _png_bytes(770, 260))

        ensure_core_on_path()
        from docfiller_core.filler import fill_template

        from app.services.settings_svc import ensure_requisites
        from app.services.templates import fill_docx_with_facsimile, resolve_template_path

        db = dbmod.SessionLocal()
        try:
            org = db.get(Organization, oid)
            req = ensure_requisites(org)
        finally:
            db.close()

        src = resolve_template_path("Счёт_на_оплату.docx", oid)
        ctx = {
            "фио_клиента": "Тест",
            "сумма": "1000",
            "наименование_услуги": "Услуга",
            "номер_договора": "1",
            "дата_договора": "01.01.2026",
            "номер_счёта": "1",
            "дата_счёта": "01.01.2026",
        }
        clean = tmp_path / "clean.docx"
        fill_template(src, clean, ctx, settings=req, images=None)
        with ZipFile(clean) as zf:
            clean_media = [n for n in zf.namelist() if n.startswith("word/media/")]

        with_img = tmp_path / "with.docx"
        fill_docx_with_facsimile(
            org=org, template_name="Счёт_на_оплату.docx", context=ctx, output_path=with_img
        )
        with ZipFile(with_img) as zf:
            img_media = [n for n in zf.namelist() if n.startswith("word/media/")]
        assert len(img_media) > len(clean_media)
    finally:
        get_settings.cache_clear()
