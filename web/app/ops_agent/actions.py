"""Белый список действий ops-agent (без произвольного shell)."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ops_agent import LOG_SERVICES, RESTARTABLE_SERVICES
from app.ops_agent.mask import mask_log_text

log = logging.getLogger("dok.ops_agent")

COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "dok")
REPO_ROOT = Path(os.environ.get("OPS_REPO_ROOT", "/srv/dok"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/var/backups/dok"))
FILES_ROOT = Path(os.environ.get("FILES_ROOT", "/srv/dok/files"))
OPS_STATE = FILES_ROOT / ".ops"
DEPLOY_LOG = OPS_STATE / "redeploy.log"
DEPLOY_STATE = OPS_STATE / "redeploy_state.json"


def _docker_client():
    try:
        import docker  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Пакет docker не установлен") from exc
    return docker.from_env()


def _container_for_service(service: str):
    client = _docker_client()
    # compose labels
    filters = {
        "label": [
            f"com.docker.compose.project={COMPOSE_PROJECT}",
            f"com.docker.compose.service={service}",
        ]
    }
    containers = client.containers.list(all=True, filters=filters)
    if not containers:
        # fallback по имени
        for c in client.containers.list(all=True):
            name = (c.name or "").lstrip("/")
            if name.startswith(f"{COMPOSE_PROJECT}-{service}-") or name == f"{COMPOSE_PROJECT}-{service}-1":
                return c
        raise RuntimeError(f"Контейнер сервиса {service} не найден")
    return containers[0]


def collect_status() -> dict[str, Any]:
    mem = _mem_info()
    disk = _disk_info(str(FILES_ROOT))
    containers: list[dict[str, Any]] = []
    try:
        client = _docker_client()
        filters = {"label": [f"com.docker.compose.project={COMPOSE_PROJECT}"]}
        for c in client.containers.list(all=True, filters=filters):
            svc = (c.labels or {}).get("com.docker.compose.service") or c.name
            containers.append(
                {
                    "service": svc,
                    "name": c.name,
                    "status": c.status,
                    "image": (c.image.tags[0] if c.image.tags else str(c.image.short_id)),
                }
            )
    except Exception as exc:
        containers = [{"error": str(exc)}]

    backup = _last_backup_info()
    ssl_age = _ssl_cert_age_days()
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(),
        "uptime_sec": _uptime_sec(),
        "cpu_load": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "memory": mem,
        "disk": disk,
        "python": platform.python_version(),
        "containers": containers,
        "ssl_cert_age_days": ssl_age,
        "backup": backup,
        "git": _git_head(),
    }


def read_logs(
    service: str,
    *,
    tail: int = 200,
    level: str = "",
) -> dict[str, Any]:
    if service not in LOG_SERVICES:
        raise ValueError(f"Сервис логов не из белого списка: {service}")
    tail = max(10, min(int(tail or 200), 2000))
    container = _container_for_service(service)
    raw = container.logs(tail=tail, timestamps=True)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if service == "postgres":
        keep = []
        for ln in text.splitlines():
            low = ln.casefold()
            if any(k in low for k in ("error", "fatal", "panic", "ошибка")):
                keep.append(ln)
        text = "\n".join(keep) if keep else "(нет строк уровня error/fatal в хвосте)"
    if level:
        lvl = level.casefold()
        text = "\n".join(
            ln for ln in text.splitlines() if lvl in ln.casefold()
        )
    return {
        "ok": True,
        "service": service,
        "tail": tail,
        "text": mask_log_text(text),
    }


def restart_service(service: str) -> dict[str, Any]:
    if service not in RESTARTABLE_SERVICES:
        raise ValueError("postgres и неизвестные сервисы перезапускать нельзя")
    container = _container_for_service(service)
    container.restart(timeout=60)
    OPS_STATE.mkdir(parents=True, exist_ok=True)
    (OPS_STATE / "last_restart.json").write_text(
        json.dumps(
            {
                "service": service,
                "at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"ok": True, "service": service, "action": "restart"}


def start_redeploy(ref: str = "main") -> dict[str, Any]:
    """Запустить deploy в фоне; статус — в shared .ops."""
    ref = (ref or "main").strip()
    if not ref or len(ref) > 80 or not all(c.isalnum() or c in "._/-" for c in ref):
        raise ValueError("Некорректный ref (разрешены буквы, цифры, . _ / -)")
    OPS_STATE.mkdir(parents=True, exist_ok=True)
    state = {
        "status": "running",
        "ref": ref,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "exit_code": None,
    }
    DEPLOY_STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    DEPLOY_LOG.write_text("", encoding="utf-8")

    script = REPO_ROOT / "deploy" / "ops_redeploy.sh"
    if script.is_file():
        cmd = ["bash", str(script), ref]
    else:
        cmd = [
            "bash",
            "-lc",
            (
                f"set -euo pipefail; cd '{REPO_ROOT}'; "
                "git fetch --tags --force origin; "
                f"git checkout --force '{ref}'; "
                "./deploy/deploy.sh"
            ),
        ]

    log_f = open(DEPLOY_LOG, "ab", buffering=0)

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            code = proc.wait()
            state_done = {
                "status": "ok" if code == 0 else "error",
                "ref": ref,
                "started_at": state["started_at"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": code,
            }
            DEPLOY_STATE.write_text(
                json.dumps(state_done, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            DEPLOY_STATE.write_text(
                json.dumps(
                    {
                        "status": "error",
                        "ref": ref,
                        "started_at": state["started_at"],
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "exit_code": -1,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        finally:
            try:
                log_f.close()
            except Exception:
                pass

    import threading

    threading.Thread(target=_run, name="ops-redeploy", daemon=True).start()
    return {"ok": True, "status": "running", "ref": ref}


def redeploy_status() -> dict[str, Any]:
    if not DEPLOY_STATE.is_file():
        return {"ok": True, "status": "idle", "log_tail": ""}
    try:
        state = json.loads(DEPLOY_STATE.read_text(encoding="utf-8"))
    except Exception:
        state = {"status": "unknown"}
    log_tail = ""
    if DEPLOY_LOG.is_file():
        raw = DEPLOY_LOG.read_bytes()[-12000:]
        log_tail = mask_log_text(raw.decode("utf-8", errors="replace"))
    state["ok"] = True
    state["log_tail"] = log_tail
    return state


def run_backup_now() -> dict[str, Any]:
    script = REPO_ROOT / "deploy" / "backup.sh"
    if not script.is_file():
        raise RuntimeError(f"Нет {script}")
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(REPO_ROOT / "deploy"),
        capture_output=True,
        text=True,
        timeout=60 * 30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    out = mask_log_text((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode != 0:
        raise RuntimeError(f"backup.sh exit={proc.returncode}\n{out[-2000:]}")
    return {"ok": True, "output_tail": out[-2000:], "backups": list_backups()["items"]}


def list_backups() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if BACKUP_DIR.is_dir():
        for sub in ("daily", "monthly"):
            d = BACKUP_DIR / sub
            if not d.is_dir():
                continue
            for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if not p.is_file():
                    continue
                if p.suffix not in {".tar", ".age"} and not p.name.endswith(".tar.age"):
                    # допускаем dok_*.tar и .tar.age
                    if ".tar" not in p.name:
                        continue
                st = p.stat()
                items.append(
                    {
                        "name": f"{sub}/{p.name}",
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    }
                )
    return {"ok": True, "items": items[:100]}


def resolve_backup_path(name: str) -> Path:
    """Безопасный путь к файлу бэкапа внутри BACKUP_DIR."""
    raw = (name or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        raise ValueError("Некорректное имя бэкапа")
    path = (BACKUP_DIR / raw).resolve()
    root = BACKUP_DIR.resolve()
    if not str(path).startswith(str(root) + os.sep) and path != root:
        raise ValueError("Путь вне каталога бэкапов")
    if not path.is_file():
        raise FileNotFoundError("Файл не найден")
    return path


def cert_renew() -> dict[str, Any]:
    """Перезагрузка Caddy (перепроверка сертификатов)."""
    container = _container_for_service("caddy")
    try:
        exit_code, output = container.exec_run(
            ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
            demux=True,
        )
        text = ""
        if isinstance(output, tuple):
            text = ((output[0] or b"") + (output[1] or b"")).decode(
                "utf-8", errors="replace"
            )
        elif isinstance(output, (bytes, bytearray)):
            text = output.decode("utf-8", errors="replace")
        if exit_code != 0:
            container.restart(timeout=60)
            return {
                "ok": True,
                "action": "caddy_restart",
                "reload_failed": True,
                "detail": mask_log_text(text)[:1000],
            }
        return {"ok": True, "action": "caddy_reload", "detail": mask_log_text(text)[:1000]}
    except Exception:
        container.restart(timeout=60)
        return {"ok": True, "action": "caddy_restart", "detail": "reload недоступен, выполнен restart"}


def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "ops-agent",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _mem_info() -> dict[str, Any]:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
        data = {}
        for line in text.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()
        total_kb = int(data.get("MemTotal", "0").split()[0])
        avail_kb = int(data.get("MemAvailable", "0").split()[0])
        return {
            "total_mb": round(total_kb / 1024, 1),
            "available_mb": round(avail_kb / 1024, 1),
            "used_pct": round(100 * (1 - avail_kb / total_kb), 1) if total_kb else None,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _disk_info(path: str) -> dict[str, Any]:
    try:
        u = shutil.disk_usage(path)
        return {
            "path": path,
            "total_gb": round(u.total / (1024**3), 2),
            "free_gb": round(u.free / (1024**3), 2),
            "used_pct": round(100 * u.used / u.total, 1) if u.total else None,
        }
    except Exception as exc:
        return {"path": path, "error": str(exc)}


def _uptime_sec() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except Exception:
        return None


def _git_head() -> dict[str, Any]:
    try:
        head = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
        return {"branch": branch, "commit": head}
    except Exception as exc:
        return {"error": str(exc)}


def _last_backup_info() -> dict[str, Any]:
    items = list_backups().get("items") or []
    if not items:
        marker = OPS_STATE / "backup_ok.json"
        if marker.is_file():
            try:
                return {"marker": json.loads(marker.read_text(encoding="utf-8"))}
            except Exception:
                return {"marker": True}
        return {"items": 0}
    return {"latest": items[0], "count": len(items)}


def _ssl_cert_age_days() -> float | None:
    """Возраст сертификата Caddy (если том смонтирован)."""
    candidates = [
        Path("/data/caddy/certificates"),
        Path("/caddy_data/caddy/certificates"),
    ]
    newest: float | None = None
    for root in candidates:
        if not root.is_dir():
            continue
        for p in root.rglob("*.crt"):
            try:
                m = p.stat().st_mtime
                newest = m if newest is None else max(newest, m)
            except OSError:
                continue
    if newest is None:
        return None
    return round((time.time() - newest) / 86400, 1)
