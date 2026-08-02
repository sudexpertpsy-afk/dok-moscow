"""HTTP-клиент app → ops-agent (внутренняя сеть)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("dok.ops_agent.client")


class OpsAgentError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def ops_agent_configured() -> bool:
    s = get_settings()
    if not s.ops_agent_enabled:
        return False
    token = (s.ops_agent_token or "").encode("utf-8")
    return len(token) >= 32


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().ops_agent_token}",
        "Accept": "application/json",
    }


def _base() -> str:
    return get_settings().ops_agent_url.rstrip("/")


def agent_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    if not ops_agent_configured():
        raise OpsAgentError("Ops-agent выключен или OPS_AGENT_TOKEN < 32 байт")
    url = f"{_base()}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.request(
                method, url, headers=_headers(), params=params, json=json_body
            )
    except httpx.HTTPError as exc:
        raise OpsAgentError(f"Ops-agent недоступен: {exc}") from exc
    if r.status_code >= 400:
        detail = r.text[:500]
        try:
            detail = r.json().get("detail") or detail
        except Exception:
            pass
        raise OpsAgentError(str(detail), status_code=r.status_code)
    if "application/json" in (r.headers.get("content-type") or ""):
        return r.json()
    return r.content


def agent_stream_get(path: str, *, params: dict[str, Any] | None = None, timeout: float = 120.0):
    """Стрим ответа (скачивание бэкапа)."""
    if not ops_agent_configured():
        raise OpsAgentError("Ops-agent выключен или OPS_AGENT_TOKEN < 32 байт")
    url = f"{_base()}{path}"
    client = httpx.Client(timeout=timeout)
    try:
        r = client.send(
            client.build_request("GET", url, headers=_headers(), params=params),
            stream=True,
        )
        if r.status_code >= 400:
            client.close()
            raise OpsAgentError(r.text[:500], status_code=r.status_code)
        return r, client
    except Exception:
        client.close()
        raise
