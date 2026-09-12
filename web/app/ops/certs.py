"""Срок TLS-сертификата по handshake (W-50 A.5).

С контейнера на публичный IP того же VDS возможен hairpin NAT —
тогда connect к host:443 не проходит. Fallback: TCP на CADDY_PROBE_HOST
(по умолчанию ``caddy``) с SNI = нужный hostname.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import socket
import ssl
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("dok.ops.certs")


def host_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return raw.split("/")[0].split(":")[0]
    return (urlparse(raw).hostname or "").strip()


def _days_from_not_after(not_after: str) -> int:
    exp = dt.datetime.fromtimestamp(
        ssl.cert_time_to_seconds(str(not_after)),
        tz=dt.timezone.utc,
    )
    now = dt.datetime.now(dt.timezone.utc)
    return int((exp - now).total_seconds() // 86400)


def _handshake_days(
    *,
    connect_host: str,
    server_hostname: str,
    port: int,
    timeout: float,
) -> int | None:
    ctx = ssl.create_default_context()
    with (
        socket.create_connection((connect_host, port), timeout=timeout) as sock,
        ctx.wrap_socket(sock, server_hostname=server_hostname) as ssock,
    ):
        cert = ssock.getpeercert()
    not_after = cert.get("notAfter") if cert else None
    if not not_after:
        return None
    return _days_from_not_after(str(not_after))


def cert_days_left(
    host: str,
    *,
    port: int = 443,
    timeout: float = 10.0,
    connect_host: str | None = None,
) -> int | None:
    """Число полных суток до notAfter. None при ошибке.

    ``connect_host`` — куда открывать TCP (по умолчанию = host).
    ``host`` всегда уходит в SNI / server_hostname.
    """
    host = (host or "").strip()
    if not host:
        return None
    target = (connect_host or host).strip() or host
    try:
        return _handshake_days(
            connect_host=target,
            server_hostname=host,
            port=port,
            timeout=timeout,
        )
    except OSError:
        if connect_host is None:
            # Hairpin: пробуем внутренний Caddy с тем же SNI.
            caddy = (os.environ.get("CADDY_PROBE_HOST") or "caddy").strip()
            if caddy and caddy != host:
                log.info(
                    "cert_days_left: %s:%s недоступен, fallback %s SNI=%s",
                    host,
                    port,
                    caddy,
                    host,
                )
                try:
                    return _handshake_days(
                        connect_host=caddy,
                        server_hostname=host,
                        port=port,
                        timeout=timeout,
                    )
                except (OSError, TypeError, ValueError, ssl.SSLError):
                    log.exception(
                        "cert_days_left fallback failed host=%s via %s",
                        host,
                        caddy,
                    )
                    return None
        log.exception("cert_days_left failed host=%s connect=%s", host, target)
        return None
    except (TypeError, ValueError, ssl.SSLError):
        log.exception("cert_days_left parse failed host=%s", host)
        return None


def cert_days_for_hosts(hosts: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in hosts:
        if not h:
            continue
        out[h] = cert_days_left(h)
    return out
