"""Админка сервиса: организации, пользователи, инвайты, заявки, статус."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.defaults import empty_requisites
from app.deps import CurrentUser, client_ip, require_csrf, require_service_admin
from app.models import Invite, Lead, Organization, User, UserRole, utcnow
from app.nav_context import admin_nav
from app.security import get_csrf_token, new_invite_token
from app.services.audit import record_event
from app.templating import templates
from app.totp_2fa import clear_totp, notify_totp_change

router = APIRouter(prefix="/admin", tags=["admin"])

# Совместимость: меню строится из app.navigation (порядок по умолчанию)
ADMIN_NAV = admin_nav()

# Короткие ключи active из роутеров → ключи реестра NAV_REGISTRY
_ADMIN_ACTIVE_KEYS = {
    "home": "admin_home",
    "orgs": "admin_orgs",
    "organizations": "admin_orgs",
    "users": "admin_users",
    "leads": "admin_leads",
    "invites": "admin_invites",
    "templates": "admin_templates",
    "payments": "admin_payments",
    "paysettings": "admin_paysettings",
    "status": "admin_status",
    "legal": "admin_legal",
    "server": "admin_server",
    "admin_security": "admin_security",
    "security": "admin_security",
}


def _ctx(request: Request, user: CurrentUser, active: str, **extra):
    from app.services.leads import count_new_leads

    new_leads_count = extra.pop("new_leads_count", None)
    db_for_count = extra.pop("db", None)
    if new_leads_count is None:
        if db_for_count is not None:
            new_leads_count = count_new_leads(db_for_count)
        else:
            from app.db import SessionLocal

            s = SessionLocal()
            try:
                new_leads_count = count_new_leads(s)
            finally:
                s.close()
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "admin_nav": admin_nav(user),
        "active": _ADMIN_ACTIVE_KEYS.get(active, active),
        "flash_error": None,
        "flash_ok": None,
        "last_invite_link": None,
        "new_leads_count": int(new_leads_count or 0),
    }
    data.update(extra)
    return data


def _org_stats(db: Session) -> dict[int, dict]:
    user_counts = dict(
        db.execute(
            select(User.org_id, func.count())
            .where(User.org_id.is_not(None))
            .group_by(User.org_id)
        ).all()
    )
    invite_counts = dict(
        db.execute(
            select(Invite.org_id, func.count())
            .where(Invite.used_at.is_(None))
            .group_by(Invite.org_id)
        ).all()
    )
    return {
        oid: {
            "users": int(user_counts.get(oid, 0)),
            "open_invites": int(invite_counts.get(oid, 0)),
        }
        for oid in set(user_counts) | set(invite_counts)
    }


def _invite_link(_request: Request, token: str) -> str:
    """Абсолютная ссылка инвайта — только APP_BASE_URL (не request.base_url / http)."""
    settings = get_settings()
    base = settings.app_base_url.rstrip("/")
    return f"{base}/invite/{token}"


@router.get("/", response_class=HTMLResponse)
def admin_home(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    orgs_n = db.scalar(select(func.count()).select_from(Organization)) or 0
    users_n = db.scalar(select(func.count()).select_from(User)) or 0
    leads_n = db.scalar(select(func.count()).select_from(Lead)) or 0
    open_invites = (
        db.scalar(select(func.count()).select_from(Invite).where(Invite.used_at.is_(None))) or 0
    )
    recent_leads = db.scalars(select(Lead).order_by(Lead.id.desc()).limit(8)).all()
    recent_orgs = db.scalars(select(Organization).order_by(Organization.id.desc()).limit(8)).all()
    stats = _org_stats(db)
    return templates.TemplateResponse(
        request=request,
        name="admin/home.html",
        context=_ctx(
            request,
            user,
            "home",
            counts={
                "orgs": orgs_n,
                "users": users_n,
                "leads": leads_n,
                "open_invites": open_invites,
            },
            recent_leads=recent_leads,
            recent_orgs=recent_orgs,
            org_stats=stats,
        ),
    )


def _subscription_list_context(db: Session, orgs: list[Organization]) -> dict:
    from app.services.admin_subscription import (
        REASON_CHOICES,
        org_subscription_map,
        subscription_badge,
    )

    subs = org_subscription_map(db, [o.id for o in orgs])
    badges = {oid: subscription_badge(sub) for oid, sub in subs.items()}
    return {
        "subs": subs,
        "sub_badges": badges,
        "reason_choices": REASON_CHOICES,
    }


@router.get("/organizations", response_class=HTMLResponse)
def admin_organizations(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    orgs = list(db.scalars(select(Organization).order_by(Organization.id.desc())).all())
    extra = _subscription_list_context(db, orgs)
    return templates.TemplateResponse(
        request=request,
        name="admin/organizations.html",
        context=_ctx(
            request,
            user,
            "orgs",
            orgs=orgs,
            org_stats=_org_stats(db),
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
            **extra,
        ),
    )


@router.post("/organizations", response_class=HTMLResponse)
def create_organization(
    request: Request,
    name: str = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    name = name.strip()
    if not name:
        orgs = list(db.scalars(select(Organization).order_by(Organization.id.desc())).all())
        extra = _subscription_list_context(db, orgs)
        return templates.TemplateResponse(
            request=request,
            name="admin/organizations.html",
            context=_ctx(
                request,
                user,
                "orgs",
                orgs=orgs,
                org_stats=_org_stats(db),
                flash_error="Укажите название организации.",
                **extra,
            ),
            status_code=400,
        )
    org = Organization(name=name, requisites=empty_requisites())
    db.add(org)
    db.flush()
    from app.services.billing import ensure_beta_subscriptions

    ensure_beta_subscriptions(db)
    db.commit()
    return RedirectResponse(f"/admin/organizations/{org.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/organizations/{org_id}", response_class=HTMLResponse)
def admin_organization_detail(
    org_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    from app.services.admin_subscription import (
        REASON_CHOICES,
        subscription_badge,
        subscription_history,
    )
    from app.services.billing import get_current_subscription

    org = db.get(Organization, org_id)
    if org is None:
        return RedirectResponse("/admin/organizations", status_code=status.HTTP_303_SEE_OTHER)
    users = db.scalars(select(User).where(User.org_id == org_id).order_by(User.id.desc())).all()
    invites = db.scalars(
        select(Invite).where(Invite.org_id == org_id).order_by(Invite.id.desc()).limit(50)
    ).all()
    sub = get_current_subscription(db, org_id)
    source_lead = None
    if org.source_lead_id:
        source_lead = db.get(Lead, org.source_lead_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/organization_detail.html",
        context=_ctx(
            request,
            user,
            "orgs",
            db=db,
            org=org,
            users=users,
            invites=invites,
            invite_ttl=get_settings().invite_ttl_hours,
            sub=sub,
            sub_info=subscription_badge(sub),
            sub_history=subscription_history(db, org_id),
            reason_choices=REASON_CHOICES,
            source_lead=source_lead,
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


def _safe_admin_return(return_to: str, org_id: int) -> str:
    """Разрешаем только относительные пути админки (после POST из dialog/карточки)."""
    raw = (return_to or "").strip()
    if raw.startswith("/admin/") and "://" not in raw and "\n" not in raw:
        base = raw.split("?", 1)[0].split("#", 1)[0]
        if base == "/admin/organizations" or base.startswith(f"/admin/organizations/{org_id}"):
            return raw
    return f"/admin/organizations/{org_id}#subscription"


@router.post("/organizations/{org_id}/subscription", response_class=HTMLResponse)
def admin_organization_subscription(
    org_id: int,
    request: Request,
    action: str = Form("apply"),
    tariff_code: str = Form("specialist"),
    term_mode: str = Form("relative"),
    months: str = Form("1"),
    ends_on: str = Form(""),
    reason: str = Form("other"),
    reason_comment: str = Form(""),
    notify: str = Form(""),
    confirm: str = Form(""),
    totp_code: str = Form(""),
    return_to: str = Form(""),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from datetime import date as date_cls
    from urllib.parse import quote, urlencode

    from app.routers.admin_server import _require_totp
    from app.services.admin_subscription import AdminSubscriptionError, apply_admin_subscription

    dest = _safe_admin_return(return_to, org_id)

    def _redir(*, ok: str | None = None, err: str | None = None) -> RedirectResponse:
        q: dict[str, str] = {}
        if ok:
            q["ok"] = ok
        if err:
            q["err"] = err
        path, frag = (dest.split("#", 1) + [""])[:2]
        path_base, _, old_q = path.partition("?")
        url = path_base
        if q:
            url += "?" + urlencode(q)
        if frag:
            url += "#" + frag
        return RedirectResponse(url, status_code=303)

    org = db.get(Organization, org_id)
    if org is None:
        return RedirectResponse("/admin/organizations", status_code=status.HTTP_303_SEE_OTHER)

    actor = db.get(User, user.id)
    assert actor is not None

    act = "terminate" if action.strip() == "terminate" else "apply"
    months_i: int | None = None
    ends_date = None
    if act == "apply":
        if term_mode == "relative":
            try:
                months_i = int(months)
            except ValueError:
                return _redir(err="Некорректный срок")
        elif ends_on.strip():
            try:
                ends_date = date_cls.fromisoformat(ends_on.strip())
            except ValueError:
                return _redir(err="Некорректная дата")

    totp_ok = False
    if totp_code.strip() or act == "terminate" or (months_i is not None and months_i > 12):
        err = _require_totp(db, user, totp_code)
        if err and (act == "terminate" or (months_i is not None and months_i > 12)):
            return _redir(err=err)
        if err is None:
            totp_ok = True

    try:
        result = apply_admin_subscription(
            db,
            org=org,
            actor=actor,
            action=act,
            tariff_code=tariff_code,
            term_mode="absolute" if term_mode == "absolute" else "relative",
            months=months_i,
            ends_on=ends_date,
            reason=reason,
            reason_comment=reason_comment,
            notify=bool(notify),
            confirm=confirm,
            totp_ok=totp_ok,
        )
    except AdminSubscriptionError as exc:
        db.rollback()
        msg = str(exc)
        if "2FA" in msg and totp_code.strip():
            err = _require_totp(db, user, totp_code)
            if err:
                return _redir(err=err)
            try:
                result = apply_admin_subscription(
                    db,
                    org=org,
                    actor=actor,
                    action=act,
                    tariff_code=tariff_code,
                    term_mode="absolute" if term_mode == "absolute" else "relative",
                    months=months_i,
                    ends_on=ends_date,
                    reason=reason,
                    reason_comment=reason_comment,
                    notify=bool(notify),
                    confirm=confirm,
                    totp_ok=True,
                )
            except AdminSubscriptionError as exc2:
                db.rollback()
                return _redir(err=str(exc2))
        else:
            return _redir(err=msg)

    db.commit()
    if act == "terminate":
        ok = "Подписка завершена"
    elif result.payment is not None:
        ok = f"Подписка обновлена, платёж {result.payment.id}"
    else:
        ok = "Подписка обновлена"
    return _redir(ok=ok)


@router.post("/organizations/{org_id}/rename", response_class=HTMLResponse)
def rename_organization(
    org_id: int,
    request: Request,
    name: str = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    org = db.get(Organization, org_id)
    if org is None:
        return RedirectResponse("/admin/organizations", status_code=status.HTTP_303_SEE_OTHER)
    name = name.strip()
    if name:
        org.name = name
        db.commit()
    return RedirectResponse(f"/admin/organizations/{org_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).order_by(User.id.desc()).limit(200)).all()
    orgs = {o.id: o.name for o in db.scalars(select(Organization)).all()}
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context=_ctx(request, user, "users", users=users, org_names=orgs),
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
def admin_user_detail(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    org_name = None
    if target.org_id:
        org = db.get(Organization, target.org_id)
        org_name = org.name if org else str(target.org_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/user_detail.html",
        context=_ctx(
            request,
            user,
            "users",
            target=target,
            org_name=org_name,
            flash_ok=request.query_params.get("ok"),
        ),
    )


@router.post("/users/{user_id}/toggle", response_class=HTMLResponse)
def toggle_user(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)
    if target.id == user.id:
        users = db.scalars(select(User).order_by(User.id.desc()).limit(200)).all()
        orgs = {o.id: o.name for o in db.scalars(select(Organization)).all()}
        return templates.TemplateResponse(
            request=request,
            name="admin/users.html",
            context=_ctx(
                request,
                user,
                "users",
                users=users,
                org_names=orgs,
                flash_error="Нельзя отключить свою учётную запись.",
            ),
            status_code=400,
        )
    target.is_active = not target.is_active
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/reset-2fa", response_class=HTMLResponse)
def admin_reset_2fa(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not target.totp_enabled and not target.totp_secret_encrypted:
        return RedirectResponse(
            f"/admin/users/{user_id}?ok=2fa-already-off",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    clear_totp(target)
    record_event(
        db,
        type="totp_admin_reset",
        org_id=target.org_id,
        user_id=target.id,
        details={"ip": client_ip(request), "by_admin_id": user.id},
        commit=False,
    )
    db.commit()
    notify_totp_change(
        settings=get_settings(),
        email=target.email,
        enabled=False,
        by_admin=True,
    )
    return RedirectResponse(
        f"/admin/users/{user_id}?ok=2fa-reset",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _leads_page_ctx(
    request: Request,
    user: CurrentUser,
    db: Session,
    *,
    status_filter: str = "",
    flash_ok: str | None = None,
    flash_error: str | None = None,
    last_invite_link: str | None = None,
):
    from app.models import LeadStatus, Tariff
    from app.services.billing import ensure_tariffs
    from app.services.leads import (
        STATUS_FILTERS,
        STATUS_LABELS,
        LEAD_PROFILES,
        beta_defaults,
        default_org_name,
        lead_view,
    )

    ensure_tariffs(db)
    q = select(Lead).order_by(Lead.id.desc()).limit(200)
    if status_filter:
        try:
            st = LeadStatus(status_filter)
            q = q.where(Lead.status == st)
        except ValueError:
            status_filter = ""
    leads = db.scalars(q).all()
    views = [lead_view(db, L) for L in leads]
    db.commit()  # авто-синхронизация registered
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
    tariffs = db.scalars(select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.id)).all()
    def_tariff, def_months = beta_defaults(db)
    return _ctx(
        request,
        user,
        "leads",
        db=db,
        lead_views=views,
        orgs=orgs,
        tariffs=tariffs,
        status_filter=status_filter,
        status_filters=STATUS_FILTERS,
        status_labels=STATUS_LABELS,
        lead_profiles=LEAD_PROFILES,
        default_tariff=def_tariff,
        default_months=def_months,
        default_org_name=default_org_name,
        flash_ok=flash_ok or request.query_params.get("ok"),
        flash_error=flash_error or request.query_params.get("err"),
        last_invite_link=last_invite_link,
    )


@router.get("/leads", response_class=HTMLResponse)
def admin_leads(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    status_filter = (request.query_params.get("status") or "").strip()
    return templates.TemplateResponse(
        request=request,
        name="admin/leads.html",
        context=_leads_page_ctx(request, user, db, status_filter=status_filter),
    )


@router.post("/leads/{lead_id}/create-org", response_class=HTMLResponse)
def create_org_from_lead(
    lead_id: int,
    request: Request,
    org_name: str = Form(""),
    tariff_code: str = Form("organization"),
    months: int = Form(3),
    send_email_now: str | None = Form(None),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import LeadError, create_org_and_invite

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    try:
        result = create_org_and_invite(
            db,
            lead=lead,
            actor=actor,
            org_name=org_name,
            tariff_code=tariff_code,
            months=months,
            send_email_now=bool(send_email_now),
        )
    except LeadError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="admin/leads.html",
            context=_leads_page_ctx(
                request, user, db, flash_error=str(exc)
            ),
            status_code=400,
        )
    return RedirectResponse(
        f"/admin/leads?ok=Организация+создана.+Инвайт"
        f"{'+отправлен' if result.email_sent else '+создан'}#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/invite", response_class=HTMLResponse)
def invite_from_lead(
    lead_id: int,
    request: Request,
    org_id: int = Form(...),
    send_email_now: str | None = Form("1"),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import LeadError, invite_to_existing_org

    lead = db.get(Lead, lead_id)
    org = db.get(Organization, org_id)
    if lead is None or org is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    try:
        result = invite_to_existing_org(
            db,
            lead=lead,
            actor=actor,
            org=org,
            send_email_now=bool(send_email_now),
        )
    except LeadError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="admin/leads.html",
            context=_leads_page_ctx(request, user, db, flash_error=str(exc)),
            status_code=409,
        )
    return RedirectResponse(
        f"/admin/leads?ok=Инвайт+создан#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/resend", response_class=HTMLResponse)
def resend_lead_invite(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import LeadError, resend_invite

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    try:
        resend_invite(db, lead=lead, actor=actor)
    except LeadError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="admin/leads.html",
            context=_leads_page_ctx(request, user, db, flash_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(
        f"/admin/leads?ok=Инвайт+отправлен+повторно#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/reject", response_class=HTMLResponse)
def reject_lead_route(
    lead_id: int,
    request: Request,
    send_mail: str | None = Form(None),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import reject_lead

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    reject_lead(db, lead=lead, actor=actor, send_mail=bool(send_mail))
    return RedirectResponse(
        f"/admin/leads?ok=Заявка+отклонена#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/spam", response_class=HTMLResponse)
def spam_lead_route(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import mark_spam

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    mark_spam(db, lead=lead, actor=actor)
    return RedirectResponse(
        f"/admin/leads?ok=Помечено+как+спам#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/note", response_class=HTMLResponse)
def note_lead_route(
    lead_id: int,
    request: Request,
    admin_note: str = Form(""),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import save_admin_note

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    save_admin_note(db, lead=lead, actor=actor, note=admin_note)
    return RedirectResponse(
        f"/admin/leads?ok=Заметка+сохранена#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/edit", response_class=HTMLResponse)
def edit_lead_route(
    lead_id: int,
    request: Request,
    email: str = Form(""),
    profile: str = Form(""),
    inn: str = Form(""),
    comment: str = Form(""),
    admin_note: str = Form(""),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import LeadError, update_lead

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    try:
        update_lead(
            db,
            lead=lead,
            actor=actor,
            email=email,
            profile=profile,
            inn=inn,
            comment=comment,
            admin_note=admin_note,
        )
    except LeadError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="admin/leads.html",
            context=_leads_page_ctx(request, user, db, flash_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(
        f"/admin/leads?ok=Данные+заявки+сохранены#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/reopen", response_class=HTMLResponse)
def reopen_lead_route(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import LeadError, reopen_lead

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    try:
        reopen_lead(db, lead=lead, actor=actor)
    except LeadError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="admin/leads.html",
            context=_leads_page_ctx(request, user, db, flash_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(
        f"/admin/leads?ok=Заявка+возвращена+в+новые#lead-{lead_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/leads/{lead_id}/delete", response_class=HTMLResponse)
def delete_lead_route(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import User as UserModel
    from app.services.leads import delete_lead

    lead = db.get(Lead, lead_id)
    if lead is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    actor = db.get(UserModel, user.id)
    assert actor is not None
    delete_lead(db, lead=lead, actor=actor)
    return RedirectResponse(
        "/admin/leads?ok=Заявка+удалена",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/invites", response_class=HTMLResponse)
def admin_invites(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    invites = db.scalars(select(Invite).order_by(Invite.id.desc()).limit(100)).all()
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
    org_names = {o.id: o.name for o in orgs}
    return templates.TemplateResponse(
        request=request,
        name="admin/invites.html",
        context=_ctx(
            request,
            user,
            "invites",
            invites=invites,
            orgs=orgs,
            org_names=org_names,
            invite_ttl=get_settings().invite_ttl_hours,
        ),
    )


@router.post("/invites", response_class=HTMLResponse)
def create_invite(
    request: Request,
    org_id: int = Form(...),
    email: str = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    settings = get_settings()
    email_norm = email.strip().lower()
    org = db.get(Organization, org_id)
    invites = db.scalars(select(Invite).order_by(Invite.id.desc()).limit(100)).all()
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
    org_names = {o.id: o.name for o in orgs}

    def err(msg: str, code: int = 400):
        return templates.TemplateResponse(
            request=request,
            name="admin/invites.html",
            context=_ctx(
                request,
                user,
                "invites",
                invites=invites,
                orgs=orgs,
                org_names=org_names,
                flash_error=msg,
                invite_ttl=settings.invite_ttl_hours,
            ),
            status_code=code,
        )

    if org is None:
        return err("Организация не найдена.", 404)
    if "@" not in email_norm:
        return err("Некорректный e-mail.")
    if db.scalar(select(User).where(User.email == email_norm)):
        return err("Пользователь с таким e-mail уже есть.", 409)

    from app.services.limits import assert_can_add_user

    try:
        assert_can_add_user(db, org.id)
    except HTTPException as exc:
        return err(str(exc.detail), int(exc.status_code))

    token = new_invite_token()
    invite = Invite(
        org_id=org.id,
        email=email_norm,
        token=token,
        expires_at=utcnow() + timedelta(hours=settings.invite_ttl_hours),
    )
    db.add(invite)
    db.commit()
    invites = db.scalars(select(Invite).order_by(Invite.id.desc()).limit(100)).all()
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/invites.html",
        context=_ctx(
            request,
            user,
            "invites",
            invites=invites,
            orgs=orgs,
            org_names={o.id: o.name for o in orgs},
            flash_ok="Приглашение создано.",
            last_invite_link=_invite_link(request, token),
            invite_ttl=settings.invite_ttl_hours,
        ),
    )


@router.post("/invites/{invite_id}/revoke", response_class=HTMLResponse)
def revoke_invite(
    invite_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    invite = db.get(Invite, invite_id)
    if invite and invite.used_at is None:
        invite.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    return RedirectResponse("/admin/invites", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/status", response_class=HTMLResponse)
def admin_status(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    from app.services.ops import status_snapshot

    settings = get_settings()
    admin_n = (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.service_admin, User.is_active.is_(True))
        )
        or 0
    )
    checks = [
        ("Администраторы сервиса", f"{admin_n} активных", admin_n > 0),
        ("SECRET_KEY", "задан" if settings.secret_key and "dev-only" not in settings.secret_key else "dev-заглушка", "dev-only" not in (settings.secret_key or "")),
        ("DaData", "ключ задан" if settings.dadata_key else "нет ключа", bool(settings.dadata_key)),
        ("SMTP", settings.smtp_host or "не настроен", bool(settings.smtp_host)),
        ("Gotenberg URL", settings.gotenberg_url, bool(settings.gotenberg_url)),
        ("FILES_ROOT", settings.files_root, True),
        ("Шаблоны", settings.templates_dir, True),
    ]
    snap = status_snapshot(db)
    return templates.TemplateResponse(
        request=request,
        name="admin/status.html",
        context=_ctx(
            request,
            user,
            "status",
            checks=checks,
            ops=snap,
        ),
    )
