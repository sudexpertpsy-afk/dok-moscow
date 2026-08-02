"""Ops-agent: изолированный whitelist API для админки «Сервер» (W-34)."""

from __future__ import annotations

# Инвентарь белого списка (аудит-тест сверяет с маршрутами).
WHITELIST_ACTIONS: frozenset[str] = frozenset(
    {
        "status",
        "logs",
        "restart",
        "redeploy",
        "backup_now",
        "backups_list",
        "backup_download",
        "cert_renew",
        "health",
        "redeploy_status",
    }
)

RESTARTABLE_SERVICES: frozenset[str] = frozenset(
    {"app", "worker", "caddy", "gotenberg"}
)
LOG_SERVICES: frozenset[str] = frozenset(
    {"app", "worker", "caddy", "gotenberg", "postgres"}
)
