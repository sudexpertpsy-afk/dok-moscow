#!/usr/bin/env bash
# Удаление файлов документов старше срока хранения организации (W-09).
# Cron (рекомендуется): 15 4 * * * /srv/dok/deploy/purge_retention.sh >> /var/log/dok-retention.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

if [[ ! -f .env ]]; then
  echo "✗ Нет deploy/.env"
  exit 1
fi

DRY=()
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY=(--dry-run)
fi

echo "→ purge_retention ${DRY[*]:-}"
docker compose --env-file .env exec -T app \
  python -m app.scripts.purge_retention "${DRY[@]}"

echo "✓ Готово"
