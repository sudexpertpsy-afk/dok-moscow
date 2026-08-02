"""W-36: единое окно, CMS-слоты, промокоды и публичные тарифы."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response

from app.billing.payments import create_card_payment
from app.defaults import empty_requisites
from app.http_cache import apply_response_cache_headers, public_cache_recently_purged
from app.models import (
    ContentBlock,
    Organization,
    PromoCode,
    PromoCodeType,
    Subscription,
    SubscriptionPeriod,
    Tariff,
    TariffCode,
    utcnow,
)
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from app.services.cms import (
    format_price_rub,
    purge_public_cache,
    safe_markdown,
    tariff_amount_kop,
    tariff_price_label,
    validate_promo_code,
)
from conftest import login


def _org_sub(dbmod):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="W36 org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        ensure_beta_subscriptions(db)
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        db.commit()
        return org.id, sub.id
    finally:
        db.close()


def test_public_price_uses_same_amount_helper_as_init(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        tariff.price_month_kop = 123_400
        db.commit()
        label = tariff_price_label(tariff, SubscriptionPeriod.month)
        amount = tariff_amount_kop(tariff, SubscriptionPeriod.month)
    finally:
        db.close()

    page = client.get("/")
    assert page.status_code == 200
    assert label in page.text

    _org_id, sub_id = _org_sub(dbmod)
    db = dbmod.SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        fake = MagicMock()
        fake.init.return_value = {
            "Success": True,
            "PaymentId": "w36-1",
            "PaymentURL": "https://pay.test/w36-1",
            "Status": "NEW",
            "OrderId": "x",
            "ErrorCode": "0",
        }
        with patch("app.billing.payments.load_tbank_client", return_value=fake):
            pay, _url = create_card_payment(
                db,
                org_id=sub.org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=tariff_amount_kop(tariff, SubscriptionPeriod.month),
                email="payer@example.com",
            )
        assert pay.amount_kop == amount
        assert fake.init.call_args.kwargs["amount_kop"] == amount
    finally:
        db.close()


def test_promo_discount_math_and_limits(app):
    _, dbmod = app
    org_id, sub_id = _org_sub(dbmod)
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        promo = PromoCode(
            code="SAVE10",
            type=PromoCodeType.percent,
            value=10,
            tariff_codes=["specialist"],
            periods=["month"],
            valid_from=utcnow() - timedelta(days=1),
            valid_to=utcnow() + timedelta(days=1),
            max_uses=1,
            is_active=True,
        )
        db.add(promo)
        db.commit()
        result = validate_promo_code(
            db,
            code="save10",
            tariff=tariff,
            period=SubscriptionPeriod.month,
            base_amount_kop=tariff.price_month_kop,
        )
        assert result.ok is True
        assert result.discount_kop == tariff.price_month_kop // 10
        assert result.final_amount_kop == tariff.price_month_kop - result.discount_kop

        sub = db.get(Subscription, sub_id)
        fake = MagicMock()
        fake.init.return_value = {
            "Success": True,
            "PaymentId": "w36-2",
            "PaymentURL": "https://pay.test/w36-2",
            "Status": "NEW",
            "OrderId": "x",
            "ErrorCode": "0",
        }
        with patch("app.billing.payments.load_tbank_client", return_value=fake):
            pay, _url = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=tariff.price_month_kop,
                email="payer@example.com",
                promo_code="SAVE10",
            )
        db.commit()
        assert pay.discount_kop == result.discount_kop
        assert fake.init.call_args.kwargs["amount_kop"] == result.final_amount_kop
        db.refresh(promo)
        assert promo.used_count == 1

        again = validate_promo_code(
            db,
            code="SAVE10",
            tariff=tariff,
            period=SubscriptionPeriod.month,
            base_amount_kop=tariff.price_month_kop,
        )
        assert again.ok is False
        assert "Лимит" in again.message
    finally:
        db.close()


def test_fixed_promo_never_makes_zero_amount(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        promo = PromoCode(
            code="BIGFIX",
            type=PromoCodeType.fixed,
            value=tariff.price_month_kop,
            tariff_codes=[],
            periods=[],
            is_active=True,
        )
        db.add(promo)
        db.commit()
        result = validate_promo_code(
            db,
            code="BIGFIX",
            tariff=tariff,
            period=SubscriptionPeriod.month,
            base_amount_kop=tariff.price_month_kop,
        )
        assert result.ok is True
        assert result.final_amount_kop == 1
    finally:
        db.close()


def test_markdown_safety_no_raw_html():
    rendered = str(
        safe_markdown(
            "<script>alert(1)</script> **ok** [bad](javascript:alert(1)) [good](/tariffs)"
        )
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<strong>ok</strong>" in rendered
    assert "javascript:" not in rendered
    assert 'href="/tariffs"' in rendered


def test_content_fallback_for_empty_slot(app):
    client, dbmod = app
    home = client.get("/").text
    assert "Комплект документов экспертизы — за 5 минут вместо часа" in home
    assert "нумерация и журнал" in home  # feature_1 fallback
    db = dbmod.SessionLocal()
    try:
        db.add(ContentBlock(key="hero_headline", title="Hero", body_md="", status="published"))
        db.commit()
    finally:
        db.close()
    assert "Комплект документов экспертизы — за 5 минут вместо часа" in client.get("/").text

    db = dbmod.SessionLocal()
    try:
        row = db.get(ContentBlock, "hero_headline")
        row.body_md = "Новый **заголовок**"
        row.status = "published"
        db.add(
            ContentBlock(
                key="feature_1",
                title="F1",
                body_md="Слот **feature_1** из Единого окна",
                status="published",
            )
        )
        db.commit()
    finally:
        db.close()
    text = client.get("/").text
    assert "Новый <strong>заголовок</strong>" in text
    assert "Слот <strong>feature_1</strong> из Единого окна" in text
    assert "Договор + счёт + акт + ПКО одной формой" not in text  # feature_1 fallback


def test_cache_purge_flag_sets_revalidate_header(monkeypatch):
    import app.http_cache as http_cache

    monkeypatch.setattr(http_cache, "_PUBLIC_CACHE_PURGED_AT", 0.0)
    assert public_cache_recently_purged() is False
    purge_public_cache()
    assert public_cache_recently_purged() is True
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "query_string": b"",
        }
    )
    response = Response("<html></html>", media_type="text/html")
    apply_response_cache_headers(request, response)
    assert response.headers["Cache-Control"] == "public, max-age=0, must-revalidate"


def test_ensure_tariffs_does_not_clobber_admin_prices(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        tariff.price_month_kop = 777_00
        tariff.price_year_kop = 7_777_00
        tariff.blurb = "Админское описание"
        tariff.features = ["Админская фича"]
        db.commit()
        ensure_tariffs(db)
        db.commit()
        db.refresh(tariff)
        assert tariff.price_month_kop == 777_00
        assert tariff.price_year_kop == 7_777_00
        assert tariff.blurb == "Админское описание"
        assert tariff.features == ["Админская фича"]
        assert format_price_rub(tariff.price_month_kop) == "777 ₽"
    finally:
        db.close()


def test_admin_cms_pages_render(app):
    client, _dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    for path, marker in (
        ("/admin/cms/", "Единое окно"),
        ("/admin/cms/tariffs", "UPDATE_TARIFFS"),
        ("/admin/cms/content", "Markdown"),
        ("/admin/cms/promos", "Промокоды"),
        ("/admin/cms/announcements", "Объявления"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text
