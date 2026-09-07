#!/usr/bin/env bash
# Обновление Док.Москва одной командой: git pull → build → migrate → restart.
# Запуск: из корня репозитория или из deploy/: ./deploy/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

if [[ ! -f .env ]]; then
  echo "✗ Нет deploy/.env — скопируйте .env.example и заполните."
  exit 1
fi

# Мягкие проверки прод-конфига (не блокируют деплой).
if ! grep -qE '^SESSION_HTTPS_ONLY=(true|1|yes)$' .env 2>/dev/null; then
  echo "⚠ SESSION_HTTPS_ONLY не включён в deploy/.env — на HTTPS-проде задайте SESSION_HTTPS_ONLY=true"
fi
if grep -qE '^YANDEX_REDIRECT_URI=https://dok\.moscow/' .env 2>/dev/null; then
  echo "⚠ YANDEX_REDIRECT_URI указывает на dok.moscow — нужен https://app.dok.moscow/auth/yandex/callback"
fi

# Правило аудита (F-03): деплой только при зелёном полном pytest в CI/агенте.
# На сервере полный прогон не гоняем — ожидаем, что ветка уже проверена до push в main.

echo "→ git pull"
git -C "$ROOT" pull --ff-only

# W-46: seed системных шаблонов в data/templates/system (overrides не трогаем)
DATA_TPL="${DATA_TEMPLATES:-/srv/dok/data/templates}"
SEED_SRC="${TEMPLATES_SEED:-$ROOT/core/Шаблоны}"
mkdir -p "$DATA_TPL/system" "$DATA_TPL/overrides"
if command -v rsync >/dev/null 2>&1 && [[ -d "$SEED_SRC" ]]; then
  echo "→ rsync шаблонов репо → $DATA_TPL/system/"
  rsync -a --delete \
    --exclude '.deleted_templates.json' \
    "$SEED_SRC/" "$DATA_TPL/system/"
  # реестр на корне data (админ пишет сюда) — обновить из seed, если нет локального
  if [[ ! -f "$DATA_TPL/contracts_registry.json" && -f "$DATA_TPL/system/contracts_registry.json" ]]; then
    cp -a "$DATA_TPL/system/contracts_registry.json" "$DATA_TPL/contracts_registry.json"
  fi
elif [[ -d "$SEED_SRC" ]]; then
  echo "⚠ rsync не установлен — копируем seed без --delete"
  cp -a "$SEED_SRC/." "$DATA_TPL/system/"
fi

# Tombstone скрывает шаблоны в кабинете; файлы system оставляем для /obraztsy.
TEMPLATES_DIR="${TEMPLATES_DIR:-$DATA_TPL}"
if [[ -f "$TEMPLATES_DIR/.deleted_templates.json" ]]; then
  echo "→ scrub реестра tombstone-шаблонов (файлы не удаляем)"
  TEMPLATES_DIR="$TEMPLATES_DIR" python3 - <<'PY' || echo "⚠ не удалось применить tombstone шаблонов (продолжаем деплой)"
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
            # писать scrub в корневой реестр
            out = root / "contracts_registry.json"
            out.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except PermissionError:
            print("  ⚠ нет прав на contracts_registry.json — пропуск")
print(f"  tombstone entries: {len(deleted)} (DOCX сохранены для /obraztsy)")
PY
fi

mkdir -p /srv/dok/data/ops
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) deploy start" >> /srv/dok/data/ops/deploy.log

echo "→ docker compose build (app + worker + ops-agent + gotenberg с fonts-liberation)"
docker compose --env-file .env build app worker ops-agent gotenberg

echo "→ docker compose up -d"
docker compose --env-file .env up -d --remove-orphans

echo "→ ожидание health postgres / миграции (entrypoint app)"
# миграции выполняет docker-entrypoint.sh при старте app
for i in $(seq 1 30); do
  if docker compose --env-file .env ps app | grep -q "Up"; then
    break
  fi
  sleep 2
done

echo "→ статус"
docker compose --env-file .env ps
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) deploy done" >> /srv/dok/data/ops/deploy.log

echo "✓ Обновление завершено. Проверьте https://app.dok.moscow/login"
