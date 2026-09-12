#!/usr/bin/env bash
# Деплой Док.Москва (W-46 фаза D) — только образ из GHCR.
#   ./deploy/deploy.sh --tag v1.2.3 — pull ghcr.io/<owner>/dok-app:v1.2.3
#   ./deploy/deploy.sh              — если в .env задан DOK_IMAGE
#   ./deploy/deploy.sh --rollback   — предыдущий тег из deploy.log
# Сборка на сервере (compose build) отключена: без DOK_IMAGE/--tag — отказ.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
OPS_DIR="${OPS_DIR:-/srv/dok/data/ops}"
LOG="$OPS_DIR/deploy.log"
GHCR_IMAGE_DEFAULT="ghcr.io/sudexpertpsy-afk/dok-app"

cd "$DEPLOY"

if [[ ! -f .env ]]; then
  echo "✗ Нет deploy/.env — скопируйте .env.example и заполните."
  exit 1
fi

# Не source .env целиком (пароли/спецсимволы). Читаем нужные ключи.
_env_get() {
  local k="$1"
  grep -E "^${k}=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true
}
DOK_IMAGE_ENV="$(_env_get DOK_IMAGE)"
APP_VERSION_ENV="$(_env_get APP_VERSION)"
GHCR_IMAGE="$(_env_get GHCR_IMAGE)"
GHCR_IMAGE="${GHCR_IMAGE:-$GHCR_IMAGE_DEFAULT}"

TAG=""
ROLLBACK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="${2:-}"
      shift 2
      ;;
    --rollback)
      ROLLBACK=1
      shift
      ;;
    -h|--help)
      sed -n '2,6p' "$0"
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $1"
      exit 1
      ;;
  esac
done

mkdir -p "$OPS_DIR"
# Контейнеры (root) и хост (deploy) пишут в data/ops — группа+sticky.
chmod 2775 "$OPS_DIR" 2>/dev/null || chmod 775 "$OPS_DIR" 2>/dev/null || true
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

# W-50 A.1: каноническая строка результата деплоя.
# Формат: <ISO> tag=<tag> sha=<sha> status=ok|fail df=<pcent>
log_deploy_result() {
  local status="$1"
  local tag_v="${TAG:-${APP_VERSION:-none}}"
  local sha_v
  sha_v="$(docker image inspect "${DOK_IMAGE:-}" --format '{{.Id}}' 2>/dev/null | sed 's/^sha256://; s/^\(.\{12}\).*/\1/' || true)"
  sha_v="${sha_v:-unknown}"
  local df_v
  df_v="$(df --output=pcent / 2>/dev/null | tail -1 | tr -d ' ' || echo n/a)"
  log "tag=${tag_v} sha=${sha_v} status=${status} df=${df_v}"
}

previous_ok_tag_from_log() {
  # Предпоследний status=ok; fallback на legacy «deploy done tag=».
  # При одной записи возвращаем пусто — иначе PREV==текущий и prune снимет rollback-тег.
  if [[ ! -f "$LOG" ]]; then
    echo ""
    return 0
  fi
  local tags
  tags="$(grep -E 'status=ok' "$LOG" | sed -n 's/.*tag=\([^ ]*\).*/\1/p' || true)"
  if [[ -z "$tags" ]]; then
    tags="$(grep -E 'deploy done tag=' "$LOG" | sed -n 's/.*tag=\([^ ]*\).*/\1/p' || true)"
  fi
  if [[ -z "$tags" ]]; then
    echo ""
    return 0
  fi
  local n
  n="$(echo "$tags" | grep -c . || true)"
  if [[ "${n:-0}" -lt 2 ]]; then
    echo ""
    return 0
  fi
  echo "$tags" | tail -n 2 | head -n 1
}

# Тег образа работающего app (до compose up — для PREV_TAG fallback).
running_app_image_tag() {
  local img
  img="$(docker compose --env-file .env ps --format '{{.Image}}' app 2>/dev/null | head -n1 || true)"
  if [[ -z "$img" ]]; then
    img="$(docker inspect dok-app-1 --format '{{.Config.Image}}' 2>/dev/null || true)"
  fi
  if [[ -z "$img" ]]; then
    echo ""
    return 0
  fi
  # ghcr.io/.../dok-app:v1.2.2 → v1.2.2
  if [[ "$img" == *:* ]]; then
    echo "${img##*:}"
  else
    echo ""
  fi
}

if [[ "$ROLLBACK" -eq 1 ]]; then
  if [[ ! -f "$LOG" ]]; then
    echo "✗ Нет $LOG — откатывать нечего"
    exit 1
  fi
  TAG="$(previous_ok_tag_from_log)"
  if [[ -z "$TAG" ]]; then
    echo "✗ В логе нет предыдущего status=ok tag= для rollback"
    exit 1
  fi
  echo "→ rollback на tag=$TAG"
fi

# Мягкие проверки прод-конфига (не блокируют деплой).
if ! grep -qE '^SESSION_HTTPS_ONLY=(true|1|yes)$' .env 2>/dev/null; then
  echo "⚠ SESSION_HTTPS_ONLY не включён в deploy/.env — на HTTPS-проде задайте SESSION_HTTPS_ONLY=true"
fi
if grep -qE '^YANDEX_REDIRECT_URI=https://dok\.moscow/' .env 2>/dev/null; then
  echo "⚠ YANDEX_REDIRECT_URI указывает на dok.moscow — нужен https://app.dok.moscow/auth/yandex/callback"
fi

seed_templates() {
  local DATA_TPL="${DATA_TEMPLATES:-/srv/dok/data/templates}"
  local SEED_SRC="${TEMPLATES_SEED:-$ROOT/core/Шаблоны}"
  mkdir -p "$DATA_TPL/system" "$DATA_TPL/overrides"

  if [[ -d "$SEED_SRC" ]]; then
    if command -v rsync >/dev/null 2>&1; then
      echo "→ rsync шаблонов → $DATA_TPL/system/"
      rsync -a --delete --exclude '.deleted_templates.json' "$SEED_SRC/" "$DATA_TPL/system/"
    else
      echo "⚠ rsync нет — cp seed"
      cp -a "$SEED_SRC/." "$DATA_TPL/system/"
    fi
  elif [[ -n "${DOK_IMAGE:-}" ]]; then
    echo "→ seed шаблонов из образа $DOK_IMAGE"
    local cid
    cid="$(docker create "$DOK_IMAGE")"
    docker cp "$cid:/app/core/Шаблоны/." "$DATA_TPL/system/" || true
    docker rm "$cid" >/dev/null
  else
    echo "⚠ нет seed-каталога шаблонов — пропускаем"
  fi

  if [[ ! -f "$DATA_TPL/contracts_registry.json" && -f "$DATA_TPL/system/contracts_registry.json" ]]; then
    cp -a "$DATA_TPL/system/contracts_registry.json" "$DATA_TPL/contracts_registry.json"
  fi

  # Tombstone scrub реестра
  if [[ -f "$DATA_TPL/.deleted_templates.json" ]]; then
    echo "→ scrub реестра tombstone-шаблонов"
    TEMPLATES_DIR="$DATA_TPL" python3 - <<'PY' || echo "⚠ tombstone scrub skipped"
import json, os
from pathlib import Path
root = Path(os.environ["TEMPLATES_DIR"])
data = json.loads((root / ".deleted_templates.json").read_text(encoding="utf-8"))
deleted = [str(x) for x in (data.get("deleted") or []) if str(x).endswith(".docx")]
reg_path = root / "contracts_registry.json"
if not reg_path.is_file():
    reg_path = root / "system" / "contracts_registry.json"
if reg_path.is_file() and deleted:
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        reg = None
    if isinstance(reg, dict):
        contracts = reg.get("contracts")
        if isinstance(contracts, dict):
            for key, vals in list(contracts.items()):
                if isinstance(vals, list):
                    contracts[key] = [x for x in vals if x not in deleted]
        if isinstance(reg.get("self_contained"), list):
            reg["self_contained"] = [x for x in reg["self_contained"] if x not in deleted]
        try:
            (root / "contracts_registry.json").write_text(
                json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except PermissionError:
            print("  ⚠ нет прав на contracts_registry.json")
print(f"  tombstone entries: {len(deleted)}")
PY
  fi
}

smoke() {
  echo "→ smoke /healthz"
  local ok=0
  for _ in $(seq 1 30); do
    if docker compose --env-file .env exec -T app curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 2
  done
  if [[ "$ok" -ne 1 ]]; then
    echo "✗ smoke /healthz не прошёл"
    docker compose --env-file .env logs app --tail 40 || true
    log_deploy_result fail
    exit 1
  fi
  docker compose --env-file .env exec -T app curl -sf http://127.0.0.1:8000/healthz || true
}

# W-50 A.1: оставить текущий и предыдущий успешный тег dok-app (для --rollback).
# PREV: предпоследний status=ok; если пуст/равен текущему — тег app ДО compose up.
prune_old_dok_app_tags() {
  local image="${GHCR_IMAGE:-$GHCR_IMAGE_DEFAULT}"
  local keep_tag="${TAG:-${APP_VERSION:-}}"
  local prev_tag
  prev_tag="$(previous_ok_tag_from_log)"
  if [[ -z "$prev_tag" || "$prev_tag" == "$keep_tag" ]]; then
    prev_tag="${PRE_DEPLOY_TAG:-}"
  fi
  if [[ -z "$prev_tag" || "$prev_tag" == "$keep_tag" ]]; then
    prev_tag="$(running_app_image_tag)"
  fi
  if [[ "$prev_tag" == "$keep_tag" ]]; then
    prev_tag=""
  fi
  echo "→ prune dok-app: keep=${keep_tag:-none},${prev_tag:-none}"
  if [[ -z "$keep_tag" ]]; then
    echo "⚠ prune: нет текущего тега — пропуск"
    return 0
  fi
  local tag
  while IFS= read -r tag; do
    [[ -z "$tag" || "$tag" == "<none>" ]] && continue
    if [[ "$tag" == "$keep_tag" || ( -n "$prev_tag" && "$tag" == "$prev_tag" ) ]]; then
      continue
    fi
    echo "  rm ${image}:${tag}"
    docker image rm -f "${image}:${tag}" >/dev/null 2>&1 || true
  done < <(docker images "$image" --format '{{.Tag}}' 2>/dev/null || true)
  docker image prune -f >/dev/null 2>&1 || true
  local df_v
  df_v="$(df --output=pcent / 2>/dev/null | tail -1 | tr -d ' ' || echo n/a)"
  log "prune done: keep=${keep_tag},${prev_tag:-none} df=${df_v}"
}

# Только pull по тегу / DOK_IMAGE из .env (без compose build на сервере)
if [[ -n "$TAG" ]]; then
  export DOK_IMAGE="${GHCR_IMAGE}:$TAG"
  export APP_VERSION="$TAG"
elif [[ -n "${DOK_IMAGE_ENV}" ]]; then
  export DOK_IMAGE="$DOK_IMAGE_ENV"
  export APP_VERSION="${APP_VERSION_ENV:-${DOK_IMAGE##*:}}"
else
  echo "✗ Нужен образ: ./deploy.sh --tag vX.Y.Z или DOK_IMAGE=ghcr.io/... в deploy/.env"
  echo "  Сборка на сервере отключена (W-46). См. docs/w46_deploy_image.md"
  exit 1
fi

GHCR_TOKEN="$(_env_get GHCR_TOKEN)"
GHCR_USER="$(_env_get GHCR_USER)"
GHCR_USER="${GHCR_USER:-sudexpertpsy-afk}"

log "deploy start mode=pull image=${DOK_IMAGE} tag=${TAG:-${APP_VERSION:-none}}"

# Прод по образу: git-дерево не источник правды для app-кода
if [[ -n "$GHCR_TOKEN" ]]; then
  echo "→ docker login ghcr.io (GHCR_TOKEN из .env)"
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
else
  echo "→ docker login: GHCR_TOKEN не задан — ожидается уже выполненный docker login ghcr.io"
fi
echo "→ docker compose pull $DOK_IMAGE"
docker compose --env-file .env pull app worker ops-agent

seed_templates

# Маркер ожидаемой версии для /admin/status
echo "${APP_VERSION:-unknown}" > "$OPS_DIR/deployed_version"
# Для compose env при следующем up
if [[ -n "${DOK_IMAGE:-}" ]]; then
  if grep -qE '^DOK_IMAGE=' .env 2>/dev/null; then
    sed -i.bak "s|^DOK_IMAGE=.*|DOK_IMAGE=$DOK_IMAGE|" .env && rm -f .env.bak
  else
    echo "DOK_IMAGE=$DOK_IMAGE" >> .env
  fi
fi
if grep -qE '^APP_VERSION=' .env 2>/dev/null; then
  sed -i.bak "s|^APP_VERSION=.*|APP_VERSION=${APP_VERSION:-dev}|" .env && rm -f .env.bak
else
  echo "APP_VERSION=${APP_VERSION:-dev}" >> .env
fi

echo "→ docker compose up -d"
# Снимок тега ДО смены контейнера — fallback PREV для prune (пустой deploy.log).
PRE_DEPLOY_TAG="$(running_app_image_tag)"
echo "→ pre-deploy running tag=${PRE_DEPLOY_TAG:-none}"
docker compose --env-file .env up -d --remove-orphans

smoke

# Сначала status=ok в лог — затем PREV = предпоследний ok (текущий уже последний).
log_deploy_result ok
prune_old_dok_app_tags

docker compose --env-file .env ps

echo "✓ Обновление завершено. Проверьте https://app.dok.moscow/login"
