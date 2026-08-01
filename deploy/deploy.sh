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

echo "→ git pull"
git -C "$ROOT" pull --ff-only

echo "→ docker compose build"
docker compose --env-file .env build app

echo "→ docker compose up -d"
docker compose --env-file .env up -d

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
