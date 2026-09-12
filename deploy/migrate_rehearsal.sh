#!/usr/bin/env bash
# W-50.1 §1: репетиция alembic upgrade/downgrade на копии прод-бэкапа.
# Временный postgres:16 (не compose-прод). Лог: data/ops/rehearsal.log.
# Usage: ./deploy/migrate_rehearsal.sh [<backup.tar.age>]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

# shellcheck disable=SC1091
if [[ -f .env ]]; then
  # Не source целиком — только нужные ключи
  _env_get() {
    local k="$1"
    grep -E "^${k}=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true
  }
else
  _env_get() { echo ""; }
fi

OPS_DIR="${OPS_DIR:-/srv/dok/data/ops}"
if [[ ! -d "$OPS_DIR" ]]; then
  OPS_DIR="$ROOT/data/ops"
fi
mkdir -p "$OPS_DIR"
LOG="$OPS_DIR/rehearsal.log"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/dok}"
AGE_IDENTITY="${AGE_IDENTITY:-$DEPLOY/backup.key}"
PG_IMAGE="${REHEARSAL_PG_IMAGE:-postgres:16}"
APP_IMAGE="${DOK_IMAGE:-${APP_IMAGE:-$(_env_get DOK_IMAGE)}}"
REHEARSAL_PG_USER="${REHEARSAL_PG_USER:-dok}"
REHEARSAL_PG_PASS="${REHEARSAL_PG_PASS:-dok_rehearsal}"
REHEARSAL_DB="${REHEARSAL_DB:-dok_rehearsal}"

ARCHIVE="${1:-}"
if [[ -z "$ARCHIVE" ]]; then
  ARCHIVE="$(ls -1t "$BACKUP_DIR"/daily/dok_*.tar.age 2>/dev/null | head -1 || true)"
fi

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"
}

fail() {
  log "✗ $*"
  exit 1
}

if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  fail "Нет архива .tar.age (передан аргумент или $BACKUP_DIR/daily/)"
fi
if [[ ! -f "$AGE_IDENTITY" ]]; then
  fail "Нет AGE_IDENTITY=$AGE_IDENTITY"
fi

WORK="$(mktemp -d /tmp/dok-migrate-rehearsal.XXXXXX)"
CONTAINER=""
HOST_PORT=""

cleanup() {
  local rc=$?
  if [[ -n "$CONTAINER" ]]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  if [[ $rc -ne 0 ]]; then
    log "rehearsal failed rc=$rc"
  fi
  exit "$rc"
}
trap cleanup EXIT

pick_free_port() {
  python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

log "→ start archive=$(basename "$ARCHIVE") work=$WORK"

cp -a "$ARCHIVE" "$WORK/bundle.tar.age"
cp -a "$AGE_IDENTITY" "$WORK/identity.key"
chmod 600 "$WORK/identity.key"

log "→ decrypt age"
age -d -i "$WORK/identity.key" -o "$WORK/bundle.tar" "$WORK/bundle.tar.age"
tar -C "$WORK" -xf "$WORK/bundle.tar"
if [[ ! -f "$WORK/db.dump" ]]; then
  fail "В архиве нет db.dump"
fi

HOST_PORT="$(pick_free_port)"
CONTAINER="dok-rehearsal-pg-$$"
log "→ temp postgres $PG_IMAGE on 127.0.0.1:${HOST_PORT} container=$CONTAINER"

docker run -d --rm \
  --name "$CONTAINER" \
  -e POSTGRES_USER="$REHEARSAL_PG_USER" \
  -e POSTGRES_PASSWORD="$REHEARSAL_PG_PASS" \
  -e POSTGRES_DB="$REHEARSAL_DB" \
  -p "127.0.0.1:${HOST_PORT}:5432" \
  "$PG_IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U "$REHEARSAL_PG_USER" -d "$REHEARSAL_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! docker exec "$CONTAINER" pg_isready -U "$REHEARSAL_PG_USER" -d "$REHEARSAL_DB" >/dev/null 2>&1; then
  fail "postgres не готов"
fi

log "→ pg_restore"
set +e
docker exec -i "$CONTAINER" \
  pg_restore -U "$REHEARSAL_PG_USER" -d "$REHEARSAL_DB" --no-owner --clean --if-exists < "$WORK/db.dump"
rc=$?
set -e
tables="$(docker exec "$CONTAINER" \
  psql -U "$REHEARSAL_PG_USER" -d "$REHEARSAL_DB" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
if [[ "${tables:-0}" -lt 5 ]]; then
  fail "мало таблиц после restore ($tables), pg_restore rc=$rc"
fi
log "→ restore ok tables=$tables (pg_restore rc=$rc)"

DATABASE_URL="postgresql+psycopg://${REHEARSAL_PG_USER}:${REHEARSAL_PG_PASS}@127.0.0.1:${HOST_PORT}/${REHEARSAL_DB}"

run_alembic() {
  local args=("$@")
  if [[ -n "$APP_IMAGE" ]]; then
    log "→ alembic via image $APP_IMAGE: ${args[*]}"
    docker run --rm --network host \
      -e DATABASE_URL="$DATABASE_URL" \
      -e DB_URL="$DATABASE_URL" \
      "$APP_IMAGE" \
      alembic "${args[@]}"
    return $?
  fi
  if [[ -x "$ROOT/web/.venv/bin/alembic" ]]; then
    log "→ alembic via web/.venv: ${args[*]}"
    (
      cd "$ROOT/web"
      export DATABASE_URL DB_URL="$DATABASE_URL"
      PYTHONPATH=. .venv/bin/alembic "${args[@]}"
    )
    return $?
  fi
  fail "Нет APP_IMAGE/DOK_IMAGE и нет web/.venv/bin/alembic"
}

run_alembic upgrade head
run_alembic downgrade -1
run_alembic upgrade head

log "✓ rehearsal OK archive=$(basename "$ARCHIVE") port=$HOST_PORT"
