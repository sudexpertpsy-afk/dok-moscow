#!/usr/bin/env bash
# W-46 фаза B: миграция named volume dok_files → bind /srv/dok/data/files
# Запуск на VDS от deploy (нужен docker). Окно ~15 мин: app/worker/ops-agent down.
# Откат: вернуть compose с volume files + FILES_ROOT=/srv/dok/files и up -d.
set -euo pipefail

ROOT="${DOK_ROOT:-/srv/dok}"
DEPLOY="$ROOT/deploy"
DATA="$ROOT/data"
TARGET="$DATA/files"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG="$DATA/ops/migrate_files_${STAMP}.log"

mkdir -p "$DATA/files" "$DATA/ops" "$DATA/templates" "$DATA/wizard" "$DATA/legal"
exec > >(tee -a "$LOG") 2>&1

cd "$DEPLOY"
echo "[$STAMP] W-46 FILES_ROOT bind migration"
echo "→ stop app worker ops-agent"
docker compose --env-file .env stop app worker ops-agent

echo "→ copy dok_files → $TARGET"
docker run --rm \
  -v dok_files:/from:ro \
  -v "$TARGET:/to" \
  alpine:3.20 \
  sh -c 'cp -a /from/. /to/ && find /from -type f | wc -l && find /to -type f | wc -l'

FROM_N="$(docker run --rm -v dok_files:/from:ro alpine:3.20 sh -c 'find /from -type f | wc -l')"
TO_N="$(find "$TARGET" -type f | wc -l)"
echo "counts: volume=$FROM_N bind=$TO_N"
if [[ "$FROM_N" != "$TO_N" ]]; then
  echo "✗ count mismatch — abort (compose не менялся; start прежних контейнеров)"
  docker compose --env-file .env start app worker ops-agent
  exit 1
fi

# backup.sh (cron deploy) пишет маркер на хосте — нужен доступ deploy
if id deploy >/dev/null 2>&1; then
  chown -R deploy:deploy "$DATA" 2>/dev/null || sudo chown -R deploy:deploy "$DATA"
fi

# sample checksums
echo "→ sample sha256 (up to 5 files)"
docker run --rm -v dok_files:/from:ro alpine:3.20 \
  sh -c 'find /from -type f | head -5 | while read f; do sha256sum "$f"; done' \
  | tee /tmp/w46_from_sha.txt || true
( cd "$TARGET" && find . -type f | head -5 | while read f; do sha256sum "$f"; done ) \
  | tee /tmp/w46_to_sha.txt || true

mkdir -p "$TARGET/.ops"
# keep existing backup_ok if present
if [[ ! -f "$TARGET/.ops/backup_ok.json" ]]; then
  echo '{"note":"migrated W-46","at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}' \
    > "$TARGET/.ops/backup_ok.json" || true
fi

echo "→ compose up (ожидается обновлённый compose.yml с bind)"
docker compose --env-file .env up -d --remove-orphans

echo "→ wait app"
for _ in $(seq 1 40); do
  if docker compose --env-file .env exec -T app true 2>/dev/null; then
    break
  fi
  sleep 2
done

echo "→ verify FILES_ROOT in container"
docker compose --env-file .env exec -T app \
  python -c "from app.config import get_settings; print(get_settings().files_root)"

HOST_N="$(find "$TARGET" -type f | wc -l)"
CTR_N="$(docker compose --env-file .env exec -T app \
  sh -c 'find /srv/dok/data/files -type f | wc -l' | tr -d '\r')"
echo "host files=$HOST_N container files=$CTR_N"
if [[ "$HOST_N" != "$CTR_N" ]]; then
  echo "⚠ count host≠container — проверьте mounts"
  exit 1
fi

echo "✓ миграция OK; volume dok_files НЕ удалён (держать ≥7 дней)"
echo "  лог: $LOG"
echo "  удалить позже: docker volume rm dok_files"
