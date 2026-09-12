"""Self-serve регистрация на app-хосте (W-47 / W-50 B.2)."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import client_ip, get_optional_user, require_csrf
from app.passwords import password_policy_hint, validate_password
from app.rate_limit import LoginRateLimiter
from app.security import get_csrf_token, hash_password, login_user_session
from app.services.leads import (
    LeadError,
    SelfServeExistsError,
    confirm_self_serve_signup,
    normalize_signup_period,
    normalize_signup_tariff,
    self_serve_signup,
)
from app.templating import templates

router = APIRouter(tags=["signup"])

_settings = get_settings()
signup_limiter = LoginRateLimiter(
    _settings.signup_rate_limit,
    _settings.signup_rate_window_sec,
    name="signup",
)


def _billing_next(tariff: str, period: str) -> str:
    q = urlencode({"tariff": tariff, "period": period, "welcome": "1"})
    return f"/cabinet/billing/?{q}"


def _login_next(tariff: str, period: str) -> str:
    nxt = _billing_next(tariff, period)
    return f"/login?next={quote(nxt, safe='')}"


def _render(
    request: Request,
    *,
    db: Session,
    status_code: int = 200,
    flash_error: str | None = None,
    flash_ok: str | None = None,
    form: dict | None = None,
):
    from app.yandex_oauth import yandex_button_visible

    settings = get_settings()
    form = form or {}
    tariff = normalize_signup_tariff(form.get("tariff"))
    period = normalize_signup_period(form.get("period"))
    yandex_qs = urlencode({"tariff": tariff, "period": period})
    return templates.TemplateResponse(
        request=request,
        name="auth/signup.html",
        context={
            "request": request,
            "csrf_token": get_csrf_token(request),
            "app_name": settings.app_name,
            "public_base_url": settings.public_base_url.rstrip("/"),
            "password_hint": password_policy_hint(),
            "flash_error": flash_error,
            "flash_ok": flash_ok,
            "form_email": form.get("email") or "",
            "form_org_name": form.get("org_name") or "",
            "form_tariff": tariff,
            "form_period": period,
            "yandex_login_available": yandex_button_visible(db),
            "yandex_start_url": f"/auth/yandex/start?{yandex_qs}",
        },
        status_code=status_code,
    )


@router.get("/signup", response_class=HTMLResponse)
def signup_page(
    request: Request,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if user and user.org_id:
        tariff = normalize_signup_tariff(request.query_params.get("tariff"))
        period = normalize_signup_period(request.query_params.get("period"))
        return RedirectResponse(
            _billing_next(tariff, period),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return _render(
        request,
        db=db,
        form={
            "tariff": request.query_params.get("tariff"),
            "period": request.query_params.get("period"),
            "email": request.query_params.get("email"),
        },
    )


@router.post("/signup", response_class=HTMLResponse)
def signup_submit(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    email: str = Form(""),
    password: str = Form(""),
    tariff: str = Form("specialist"),
    period: str = Form("month"),
    org_name: str = Form(""),
    accept_terms: str | None = Form(None),
    website: str = Form(""),  # honeypot
):
    tariff_n = normalize_signup_tariff(tariff)
    period_n = normalize_signup_period(period)
    form = {
        "email": email,
        "org_name": org_name,
        "tariff": tariff_n,
        "period": period_n,
    }

    # honeypot
    if (website or "").strip():
        return RedirectResponse("/signup", status_code=status.HTTP_303_SEE_OTHER)

    ip = client_ip(request)
    key = f"ip:{ip}"
    if signup_limiter.is_blocked(key):
        return _render(
            request,
            db=db,
            form=form,
            flash_error="Слишком много попыток регистрации. Попробуйте позже.",
            status_code=429,
        )
    # считаем каждую попытку (анти-абьюз), не только ошибки
    signup_limiter.register_failure(key)

    if not accept_terms:
        return _render(
            request,
            db=db,
            form=form,
            flash_error="Примите оферту и политику ПДн.",
            status_code=400,
        )

    err = validate_password(password, email=email)
    if err:
        return _render(request, db=db, form=form, flash_error=err, status_code=400)

    try:
        result = self_serve_signup(
            db,
            email=email,
            password_hash=hash_password(password),
            tariff_code=tariff_n,
            period=period_n,
            org_name=org_name or None,
        )
    except SelfServeExistsError:
        return RedirectResponse(
            _login_next(tariff_n, period_n) + "&msg=exists",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except LeadError as exc:
        db.rollback()
        return _render(
            request, db=db, form=form, flash_error=str(exc), status_code=400
        )
    except Exception:
        db.rollback()
        raise

    msg = (
        "Мы отправили письмо со ссылкой ещё раз. Подтвердите e-mail, чтобы создать кабинет."
        if result.resent
        else "Проверьте почту: мы отправили ссылку для подтверждения регистрации. "
        "Кабинет создастся после перехода по ссылке."
    )
    return _render(request, db=db, form=form, flash_ok=msg)


@router.get("/confirm-signup/{token}", response_class=HTMLResponse)
def confirm_signup(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    """W-50 B.2: подтверждение Signup → org + user + guest."""
    try:
        result = confirm_self_serve_signup(db, token)
    except SelfServeExistsError:
        return RedirectResponse(
            "/login?msg=exists",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except LeadError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "request": request,
                "csrf_token": get_csrf_token(request),
                "app_name": get_settings().app_name,
                "flash_error": str(exc),
                "password_hint": password_policy_hint(),
            },
            status_code=400,
        )
    except Exception:
        db.rollback()
        raise

    if result is None or result.user is None:
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "request": request,
                "csrf_token": get_csrf_token(request),
                "app_name": get_settings().app_name,
                "flash_error": "Ссылка подтверждения недействительна или истекла.",
                "password_hint": password_policy_hint(),
            },
            status_code=400,
        )

    login_user_session(
        request, result.user.id, result.user.org_id, result.user.role.value
    )
    return RedirectResponse(
        _billing_next(result.tariff_code, result.period),
        status_code=status.HTTP_303_SEE_OTHER,
    )
