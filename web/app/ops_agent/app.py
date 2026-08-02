"""HTTP API ops-agent: только внутренняя сеть compose + Bearer token."""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.ops_agent import WHITELIST_ACTIONS
from app.ops_agent import actions

log = logging.getLogger("dok.ops_agent.api")

_RATE_LOCK = threading.Lock()
_RATE: dict[str, list[float]] = {}
_RATE_LIMIT = int(os.environ.get("OPS_AGENT_RATE_LIMIT", "30"))
_RATE_WINDOW = float(os.environ.get("OPS_AGENT_RATE_WINDOW_SEC", "60"))


def _token() -> str:
    return (os.environ.get("OPS_AGENT_TOKEN") or "").strip()


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = _token()
    if len(expected.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="OPS_AGENT_TOKEN не настроен (≥32 байт)")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Нужен Bearer token")
    got = authorization.split(" ", 1)[1].strip()
    if not got or not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="Неверный token")


def rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE.setdefault(ip, [])
        bucket[:] = [t for t in bucket if now - t < _RATE_WINDOW]
        if len(bucket) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="rate limit")
        bucket.append(now)


def create_ops_agent_app() -> FastAPI:
    app = FastAPI(title="Dok Moscow ops-agent", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def _unknown_guard(request: Request, call_next):
        # отказ на неизвестные пути (кроме health без префикса ошибок)
        response = await call_next(request)
        return response

    @app.get("/v1/health")
    def health(_: None = Depends(require_token), __: None = Depends(rate_limit)) -> dict[str, Any]:
        return actions.health()

    @app.get("/v1/status")
    def status(_: None = Depends(require_token), __: None = Depends(rate_limit)) -> dict[str, Any]:
        return actions.collect_status()

    @app.get("/v1/logs")
    def logs(
        service: str = Query(...),
        tail: int = Query(200, ge=10, le=2000),
        level: str = Query(""),
        _: None = Depends(require_token),
        __: None = Depends(rate_limit),
    ) -> dict[str, Any]:
        try:
            return actions.read_logs(service, tail=tail, level=level)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    class RestartBody(BaseModel):
        service: str

    @app.post("/v1/restart")
    def restart(
        body: RestartBody,
        _: None = Depends(require_token),
        __: None = Depends(rate_limit),
    ) -> dict[str, Any]:
        try:
            return actions.restart_service(body.service)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    class RedeployBody(BaseModel):
        ref: str = Field(default="main", max_length=80)

    @app.post("/v1/redeploy")
    def redeploy(
        body: RedeployBody,
        _: None = Depends(require_token),
        __: None = Depends(rate_limit),
    ) -> dict[str, Any]:
        try:
            return actions.start_redeploy(body.ref)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/v1/redeploy/status")
    def redeploy_status(
        _: None = Depends(require_token), __: None = Depends(rate_limit)
    ) -> dict[str, Any]:
        return actions.redeploy_status()

    @app.post("/v1/backup")
    def backup_now(
        _: None = Depends(require_token), __: None = Depends(rate_limit)
    ) -> dict[str, Any]:
        try:
            return actions.run_backup_now()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/v1/backups")
    def backups_list(
        _: None = Depends(require_token), __: None = Depends(rate_limit)
    ) -> dict[str, Any]:
        return actions.list_backups()

    @app.get("/v1/backups/file")
    def backup_download(
        name: str = Query(...),
        _: None = Depends(require_token),
        __: None = Depends(rate_limit),
    ):
        try:
            path = actions.resolve_backup_path(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            filename=path.name,
            media_type="application/octet-stream",
        )

    @app.post("/v1/cert_renew")
    def cert_renew(
        _: None = Depends(require_token), __: None = Depends(rate_limit)
    ) -> dict[str, Any]:
        try:
            return actions.cert_renew()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def reject_unknown(path: str) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "error": "unknown_action", "path": path},
            status_code=404,
        )

    # инвентарь для самопроверки
    app.state.whitelist = sorted(WHITELIST_ACTIONS)
    return app


app = create_ops_agent_app()
