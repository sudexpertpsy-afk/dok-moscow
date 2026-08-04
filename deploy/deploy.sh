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

# Шаблоны DOCX в core/Шаблоны отслеживаются git'ом: checkout/pull возвращает
# файлы, удалённые через /admin/templates. Tombstone (.deleted_templates.json)
# хранит список удалений — снимаем вернувшиеся файлы до перезапуска контейнеров.
TEMPLATES_DIR="${TEMPLATES_DIR:-$ROOT/core/Шаблоны}"
if [[ -f "$TEMPLATES_DIR/.deleted_templates.json" ]]; then
  echo "→ scrub удалённых шаблонов (tombstone)"
  TEMPLATES_DIR="$TEMPLATES_DIR" CORE_PATH="$ROOT/core" \
    PYTHONPATH="$ROOT/web${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - <<'PY' || echo "⚠ не удалось применить tombstone шаблонов (продолжаем деплой)"
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["PYTHONPATH"].split(":")[0])))
os.environ.setdefault("TEMPLATES_DIR", os.environ["TEMPLATES_DIR"])
os.environ.setdefault("CORE_PATH", os.environ.get("CORE_PATH", ""))
from app.config import get_settings
get_settings.cache_clear()
from app.services.template_admin import apply_deleted_templates
removed = apply_deleted_templates(Path(os.environ["TEMPLATES_DIR"]))
print(f"  убрано файлов: {len(removed)}")
for name in removed:
    print(f"  - {name}")
PY
fi

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

echo "✓ Обновление завершено. Проверьте https://app.dok.moscow/login"
