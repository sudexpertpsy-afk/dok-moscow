"""Админка «Сервер» — ops-agent UI (W-34)."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, client_ip, require_csrf, require_service_admin
from app.models import OpsJournalEntry, User
from app.routers.admin import _ctx
from app.services.audit import record_event
from app.services.hostland import load_hostland, save_hostland
from app.services.ops_agent_client import (
    OpsAgentError,
    agent_request,
    agent_stream_get,
    ops_agent_configured,
)
from app.templating import templates
from app.totp_2fa import verify_user_totp_or_backup

router = APIRouter(prefix="/admin/server", tags=["admin-server"])


def _journal(
    db: Session,
    *,
    user: CurrentUser,
    action: str,
    ok: bool,
    ip: str | None,
    details: dict,
) -> None:
    db.add(
        OpsJournalEntry(
            user_id=user.id,
            action=action,
            ok=ok,
            ip=ip,
            details=details or {},
        )
    )
    record_event(
        db,
        type=f"ops_{action}",
        org_id=None,
        user_id=user.id,
        details={"ok": ok, "ip": ip, **(details or {})},
    )


def _require_totp(db: Session, user: CurrentUser, totp_code: str) -> str | None:
    row = db.get(User, user.id)
    if row is None or not row.totp_enabled:
        return "Для действий изменения включите 2FA в настройках профиля"
    if not verify_user_totp_or_backup(row, (totp_code or "").strip()):
        return "Неверный код 2FA"
    return None


def _err(msg: str) -> RedirectResponse:
    return RedirectResponse(
        f"/admin/server/?err={quote(msg, safe='')}", status_code=303
    )


def _ok(msg: str) -> RedirectResponse:
    return RedirectResponse(
        f"/admin/server/?ok={quote(msg, safe='')}", status_code=303
    )


@router.get("/", response_class=HTMLResponse)
def server_home(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    configured = ops_agent_configured()
    status = None
    status_error = None
    if configured:
        try:
            status = agent_request("GET", "/v1/status", timeout=20.0)
        except OpsAgentError as exc:
            status_error = str(exc)
    backups = []
    if configured and not status_error:
        try:
            backups = (agent_request("GET", "/v1/backups", timeout=20.0) or {}).get(
                "items"
            ) or []
        except OpsAgentError:
            backups = []
    journal = list(
        db.scalars(
            select(OpsJournalEntry)
            .order_by(OpsJournalEntry.id.desc())
            .limit(30)
        ).all()
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/server.html",
        context=_ctx(
            request,
            user,
            "server",
            configured=configured,
            enabled=settings.ops_agent_enabled,
            status=status,
            status_error=status_error,
            backups=backups,
            hostland=load_hostland(),
            journal=journal,
            flash_error=request.query_params.get("err"),
            flash_ok=request.query_params.get("ok"),
            log_text=unquote(request.query_params.get("log") or ""),
            log_service=request.query_params.get("svc") or "app",
            redeploy_ref=request.query_params.get("ref") or "main",
            state={"status": "idle", "log_tail": ""},
        ),
    )


@router.get("/status-fragment", response_class=HTMLResponse)
def status_fragment(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    status = None
    status_error = None
    if ops_agent_configured():
        try:
            status = agent_request("GET", "/v1/status", timeout=15.0)
        except OpsAgentError as exc:
            status_error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="admin/_server_status.html",
        context={
            "request": request,
            "status": status,
            "status_error": status_error,
            "configured": ops_agent_configured(),
        },
    )


@router.get("/redeploy-fragment", response_class=HTMLResponse)
def redeploy_fragment(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    state = {"status": "idle", "log_tail": ""}
    if ops_agent_configured():
        try:
            state = agent_request("GET", "/v1/redeploy/status", timeout=15.0)
        except OpsAgentError as exc:
            state = {"status": "error", "log_tail": str(exc)}
    return templates.TemplateResponse(
        request=request,
        name="admin/_server_redeploy.html",
        context={"request": request, "state": state},
    )


@router.get("/agent-ping")
def agent_ping(user: CurrentUser = Depends(require_service_admin)):
    """Прокси health агента (поллинг переживает рестарт app с ретраями на клиенте)."""
    if not ops_agent_configured():
        return {"ok": False, "error": "disabled"}
    try:
        data = agent_request("GET", "/v1/health", timeout=5.0)
        return data
    except OpsAgentError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/logs")
def server_logs(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    service: str = Form("app"),
    tail: str = Form("200"),
    level: str = Form(""),
):
    if not ops_agent_configured():
        return _err("Ops-agent выключен")
    try:
        data = agent_request(
            "GET",
            "/v1/logs",
            params={
                "service": service,
                "tail": int(tail or 200),
                "level": level or "",
            },
            timeout=30.0,
        )
        _journal(
            db,
            user=user,
            action="logs",
            ok=True,
            ip=client_ip(request),
            details={"service": service},
        )
        db.commit()
        text = quote((data.get("text") or "")[:12000], safe="")
        return RedirectResponse(
            f"/admin/server/?svc={quote(service)}&log={text}",
            status_code=303,
        )
    except OpsAgentError as exc:
        _journal(
            db,
            user=user,
            action="logs",
            ok=False,
            ip=client_ip(request),
            details={"service": service, "error": str(exc)},
        )
        db.commit()
        return _err(str(exc))


@router.post("/restart")
def server_restart(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    service: str = Form(...),
    totp_code: str = Form(""),
    confirm: str = Form(""),
):
    if confirm != "RESTART":
        return _err("Введите RESTART для подтверждения")
    err = _require_totp(db, user, totp_code)
    if err:
        return _err(err)
    if not ops_agent_configured():
        return _err("Ops-agent выключен")
    try:
        agent_request(
            "POST", "/v1/restart", json_body={"service": service}, timeout=90.0
        )
        _journal(
            db,
            user=user,
            action="restart",
            ok=True,
            ip=client_ip(request),
            details={"service": service},
        )
        db.commit()
        return _ok(f"Перезапуск {service} поставлен")
    except OpsAgentError as exc:
        _journal(
            db,
            user=user,
            action="restart",
            ok=False,
            ip=client_ip(request),
            details={"service": service, "error": str(exc)},
        )
        db.commit()
        return _err(str(exc))


@router.post("/redeploy")
def server_redeploy(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    ref: str = Form("main"),
    totp_code: str = Form(""),
    confirm: str = Form(""),
):
    if confirm != "REDEPLOY":
        return _err("Введите REDEPLOY для подтверждения")
    err = _require_totp(db, user, totp_code)
    if err:
        return _err(err)
    if not ops_agent_configured():
        return _err("Ops-agent выключен")
    try:
        agent_request(
            "POST", "/v1/redeploy", json_body={"ref": ref or "main"}, timeout=30.0
        )
        _journal(
            db,
            user=user,
            action="redeploy",
            ok=True,
            ip=client_ip(request),
            details={"ref": ref},
        )
        db.commit()
        return RedirectResponse(
            f"/admin/server/?ok={quote('Деплой запущен')}&ref={quote(ref or 'main')}",
            status_code=303,
        )
    except OpsAgentError as exc:
        _journal(
            db,
            user=user,
            action="redeploy",
            ok=False,
            ip=client_ip(request),
            details={"ref": ref, "error": str(exc)},
        )
        db.commit()
        return _err(str(exc))


@router.post("/backup")
def server_backup(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    totp_code: str = Form(""),
):
    err = _require_totp(db, user, totp_code)
    if err:
        return _err(err)
    if not ops_agent_configured():
        return _err("Ops-agent выключен")
    try:
        agent_request("POST", "/v1/backup", timeout=60 * 35)
        _journal(
            db,
            user=user,
            action="backup_now",
            ok=True,
            ip=client_ip(request),
            details={},
        )
        db.commit()
        return _ok("Бэкап выполнен")
    except OpsAgentError as exc:
        _journal(
            db,
            user=user,
            action="backup_now",
            ok=False,
            ip=client_ip(request),
            details={"error": str(exc)},
        )
        db.commit()
        return _err(str(exc))


@router.get("/backup/download")
def server_backup_download(
    request: Request,
    name: str = Query(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    if not ops_agent_configured():
        return _err("Ops-agent выключен")
    try:
        resp, client = agent_stream_get(
            "/v1/backups/file", params={"name": name}, timeout=300.0
        )
        _journal(
            db,
            user=user,
            action="backup_download",
            ok=True,
            ip=client_ip(request),
            details={"name": name},
        )
        db.commit()

        def _iter():
            try:
                for chunk in resp.iter_bytes():
                    yield chunk
            finally:
                resp.close()
                client.close()

        filename = name.rsplit("/", 1)[-1]
        return StreamingResponse(
            _iter(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except OpsAgentError as exc:
        _journal(
            db,
            user=user,
            action="backup_download",
            ok=False,
            ip=client_ip(request),
            details={"name": name, "error": str(exc)},
        )
        db.commit()
        return _err(str(exc))


@router.post("/cert-renew")
def server_cert_renew(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    totp_code: str = Form(""),
):
    err = _require_totp(db, user, totp_code)
    if err:
        return _err(err)
    if not ops_agent_configured():
        return _err("Ops-agent выключен")
    try:
        agent_request("POST", "/v1/cert_renew", timeout=90.0)
        _journal(
            db,
            user=user,
            action="cert_renew",
            ok=True,
            ip=client_ip(request),
            details={},
        )
        db.commit()
        return _ok("Перезагрузка Caddy / сертификат")
    except OpsAgentError as exc:
        _journal(
            db,
            user=user,
            action="cert_renew",
            ok=False,
            ip=client_ip(request),
            details={"error": str(exc)},
        )
        db.commit()
        return _err(str(exc))


@router.post("/hostland")
def server_hostland_save(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    panel_url: str = Form(""),
    pay_url: str = Form(""),
    console_url: str = Form(""),
    vds_paid_until: str = Form(""),
):
    until: date | None | str
    raw = (vds_paid_until or "").strip()
    if raw:
        try:
            until = date.fromisoformat(raw)
        except ValueError:
            return _err("Дата VDS: ожидается YYYY-MM-DD")
    else:
        until = ""
    data = save_hostland(
        panel_url=panel_url,
        pay_url=pay_url,
        console_url=console_url,
        vds_paid_until=until,
    )
    _journal(
        db,
        user=user,
        action="hostland_save",
        ok=True,
        ip=client_ip(request),
        details={"vds_paid_until": data.get("vds_paid_until")},
    )
    db.commit()
    return _ok("Hostland сохранён")
