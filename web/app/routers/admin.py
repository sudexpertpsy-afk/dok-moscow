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
}


def _ctx(request: Request, user: CurrentUser, active: str, **extra):
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


def _invite_link(request: Request, token: str) -> str:
    settings = get_settings()
    base = (settings.app_base_url or str(request.base_url)).rstrip("/")
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


@router.get("/organizations", response_class=HTMLResponse)
def admin_organizations(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    orgs = db.scalars(select(Organization).order_by(Organization.id.desc())).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/organizations.html",
        context=_ctx(request, user, "orgs", orgs=orgs, org_stats=_org_stats(db)),
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
        orgs = db.scalars(select(Organization).order_by(Organization.id.desc())).all()
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
    org = db.get(Organization, org_id)
    if org is None:
        return RedirectResponse("/admin/organizations", status_code=status.HTTP_303_SEE_OTHER)
    users = db.scalars(select(User).where(User.org_id == org_id).order_by(User.id.desc())).all()
    invites = db.scalars(
        select(Invite).where(Invite.org_id == org_id).order_by(Invite.id.desc()).limit(50)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/organization_detail.html",
        context=_ctx(
            request,
            user,
            "orgs",
            org=org,
            users=users,
            invites=invites,
            invite_ttl=get_settings().invite_ttl_hours,
        ),
    )


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


@router.get("/leads", response_class=HTMLResponse)
def admin_leads(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    leads = db.scalars(select(Lead).order_by(Lead.id.desc()).limit(100)).all()
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/leads.html",
        context=_ctx(request, user, "leads", leads=leads, orgs=orgs),
    )


@router.post("/leads/{lead_id}/invite", response_class=HTMLResponse)
def invite_from_lead(
    lead_id: int,
    request: Request,
    org_id: int = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    lead = db.get(Lead, lead_id)
    org = db.get(Organization, org_id)
    if lead is None or org is None:
        return RedirectResponse("/admin/leads", status_code=status.HTTP_303_SEE_OTHER)
    email_norm = lead.email.strip().lower()
    if db.scalar(select(User).where(User.email == email_norm)):
        leads = db.scalars(select(Lead).order_by(Lead.id.desc()).limit(100)).all()
        orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
        return templates.TemplateResponse(
            request=request,
            name="admin/leads.html",
            context=_ctx(
                request,
                user,
                "leads",
                leads=leads,
                orgs=orgs,
                flash_error=f"Пользователь {email_norm} уже зарегистрирован.",
            ),
            status_code=409,
        )
    settings = get_settings()
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
            flash_ok="Приглашение создано из заявки.",
            last_invite_link=_invite_link(request, token),
            invite_ttl=get_settings().invite_ttl_hours,
        ),
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
