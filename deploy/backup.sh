#!/usr/bin/env bash
# Ночной бэкап: pg_dump + архив files → (опционально age) → локально / внешнее хранилище.
# Ротация: 30 ежедневных, 12 месячных.
# Cron: 30 3 * * * /srv/dok/deploy/backup.sh >> /var/log/dok-backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

# shellcheck disable=SC1091
source .env

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dok}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
DAY="$(date -u +%Y-%m-%d)"
MONTH="$(date -u +%Y-%m)"
DAILY_DIR="$BACKUP_DIR/daily"
MONTHLY_DIR="$BACKUP_DIR/monthly"
WORK="$(mktemp -d /tmp/dok-backup.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$DAILY_DIR" "$MONTHLY_DIR"

echo "[$STAMP] → pg_dump"
docker compose --env-file .env exec -T postgres \
  pg_dump -U dok -d dok --no-owner --format=custom \
  > "$WORK/db.dump"

echo "[$STAMP] → архив файлов"
# том files смонтирован в app:/srv/dok/files (имя проекта compose: dok)
if docker compose --env-file .env ps --status running -q app >/dev/null 2>&1; then
  docker compose --env-file .env exec -T app \
    tar -C /srv/dok/files -czf - . > "$WORK/files.tar.gz"
else
  docker compose --env-file .env run --rm --no-deps \
    -v dok_files:/data:ro alpine:3.20 \
    tar -C /data -czf - . > "$WORK/files.tar.gz"
fi

ARCHIVE="$WORK/dok_${STAMP}.tar"
tar -C "$WORK" -cf "$ARCHIVE" db.dump files.tar.gz
rm -f "$WORK/db.dump" "$WORK/files.tar.gz"

OUT="$DAILY_DIR/dok_${DAY}.tar"
if [[ -n "${AGE_RECIPIENT:-}" ]] && command -v age >/dev/null 2>&1; then
  echo "[$STAMP] → шифрование age"
  age -r "$AGE_RECIPIENT" -o "${OUT}.age" "$ARCHIVE"
  rm -f "$ARCHIVE"
  OUT="${OUT}.age"
else
  mv "$ARCHIVE" "$OUT"
fi

# копия в месячные (1-е число или первый бэкап месяца)
shopt -s nullglob
monthly_existing=("$MONTHLY_DIR"/dok_"${MONTH}".tar*)
shopt -u nullglob
if [[ "$(date -u +%d)" == "01" ]] || [[ ${#monthly_existing[@]} -eq 0 ]]; then
  cp -a "$OUT" "$MONTHLY_DIR/"
fi

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  echo "[$STAMP] → выгрузка на $BACKUP_REMOTE"
  if [[ "$BACKUP_REMOTE" == s3://* ]]; then
    if command -v aws >/dev/null 2>&1; then
      aws s3 cp "$OUT" "$BACKUP_REMOTE/"
    else
      echo "⚠ aws cli не найден — пропуск удалённой выгрузки"
    fi
  else
    scp -o StrictHostKeyChecking=accept-new "$OUT" "$BACKUP_REMOTE/"
  fi
fi

echo "[$STAMP] → ротация (30 daily / 12 monthly)"
ls -1t "$DAILY_DIR"/dok_* 2>/dev/null | tail -n +31 | xargs -r rm -f
ls -1t "$MONTHLY_DIR"/dok_* 2>/dev/null | tail -n +13 | xargs -r rm -f

echo "[$STAMP] ✓ бэкап: $OUT"
