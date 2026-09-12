#!/usr/bin/env bash
# Восстановление из архива бэкапа на чистый (или существующий) инстанс.
# Использование: ./restore.sh /var/backups/dok/daily/dok_2026-07-31.tar[.age]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

ARCHIVE="${1:-}"
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "Использование: $0 /path/to/dok_YYYY-MM-DD.tar[.age]"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "✗ Нет deploy/.env"
  exit 1
fi

# shellcheck disable=SC1091
source .env

WORK="$(mktemp -d /tmp/dok-restore.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

echo "→ распаковка $ARCHIVE"
if [[ "$ARCHIVE" == *.age ]]; then
  if [[ -z "${AGE_IDENTITY:-}" ]]; then
    echo "✗ Для .age задайте AGE_IDENTITY=/path/to/key.txt"
    exit 1
  fi
  age -d -i "$AGE_IDENTITY" -o "$WORK/bundle.tar" "$ARCHIVE"
  tar -C "$WORK" -xf "$WORK/bundle.tar"
elif [[ "$ARCHIVE" == *.gpg ]]; then
  gpg --batch --yes -o "$WORK/bundle.tar" -d "$ARCHIVE"
  tar -C "$WORK" -xf "$WORK/bundle.tar"
else
  tar -C "$WORK" -xf "$ARCHIVE"
fi

if [[ ! -f "$WORK/db.dump" ]]; then
  echo "✗ В архиве нет db.dump"
  exit 1
fi

echo "→ подъём postgres (если ещё не запущен)"
docker compose --env-file .env up -d postgres
for _ in $(seq 1 30); do
  if docker compose --env-file .env exec -T postgres pg_isready -U dok -d dok >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "→ восстановление БД (пересоздание схемы)"
docker compose --env-file .env exec -T postgres \
  psql -U dok -d dok -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO dok;"

docker compose --env-file .env exec -T postgres \
  pg_restore -U dok -d dok --no-owner --clean --if-exists < "$WORK/db.dump" \
  || docker compose --env-file .env exec -T postgres \
       pg_restore -U dok -d dok --no-owner < "$WORK/db.dump"

if [[ -f "$WORK/files.tar.gz" ]]; then
  echo "→ восстановление файлов"
  docker compose --env-file .env up -d app
  docker compose --env-file .env exec -T app sh -c 'rm -rf /srv/dok/files/*'
  docker compose --env-file .env exec -T app tar -C /srv/dok/files -xzf - < "$WORK/files.tar.gz"
fi

echo "→ перезапуск приложения"
docker compose --env-file .env up -d

echo "✓ Восстановление завершено. Проверьте вход в кабинет."
