#!/usr/bin/env bash
# Restore drill (аудит F-04): расшифровать последний .tar.age во временный каталог,
# развернуть db.dump в отдельную БД dok_drill, сверить counts таблиц с продом.
# Прод НЕ трогает. Запуск на VDS: sudo ./deploy/restore_drill.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

# shellcheck disable=SC1091
source .env

BACKUP_DIR="${BACKUP_DIR:-/var/backups/dok}"
AGE_IDENTITY="${AGE_IDENTITY:-$DEPLOY/backup.key}"
ARCHIVE="${1:-}"
if [[ -z "$ARCHIVE" ]]; then
  ARCHIVE="$(ls -1t "$BACKUP_DIR"/daily/dok_*.tar.age 2>/dev/null | head -1 || true)"
fi
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "✗ Нет архива .tar.age в $BACKUP_DIR/daily/"
  exit 1
fi
if [[ ! -f "$AGE_IDENTITY" ]]; then
  echo "✗ Нет AGE_IDENTITY=$AGE_IDENTITY"
  exit 1
fi

WORK="$(mktemp -d /tmp/dok-restore-drill.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

echo "→ архив: $ARCHIVE"
echo "→ работа: $WORK"
cp -a "$ARCHIVE" "$WORK/bundle.tar.age"
cp -a "$AGE_IDENTITY" "$WORK/identity.key"
chmod 600 "$WORK/identity.key"

echo "→ расшифровка age"
age -d -i "$WORK/identity.key" -o "$WORK/bundle.tar" "$WORK/bundle.tar.age"
tar -C "$WORK" -xf "$WORK/bundle.tar"
if [[ ! -f "$WORK/db.dump" ]]; then
  echo "✗ В архиве нет db.dump"
  exit 1
fi

echo "→ подъём postgres (если нужно)"
docker compose --env-file .env up -d postgres
for i in $(seq 1 30); do
  if docker compose --env-file .env exec -T postgres pg_isready -U dok -d dok >/dev/null 2>/dev/null; then
    break
  fi
  sleep 2
done

DRILL_DB="dok_drill"
echo "→ временная БД $DRILL_DB"
docker compose --env-file .env exec -T postgres \
  psql -U dok -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DRILL_DB;"
docker compose --env-file .env exec -T postgres \
  psql -U dok -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DRILL_DB OWNER dok;"

echo "→ pg_restore → $DRILL_DB"
set +e
docker compose --env-file .env exec -T postgres \
  pg_restore -U dok -d "$DRILL_DB" --no-owner --clean --if-exists < "$WORK/db.dump"
rc=$?
set -e
# pg_restore часто возвращает 1 из‑за notice — проверяем наличие таблиц
tables="$(docker compose --env-file .env exec -T postgres \
  psql -U dok -d "$DRILL_DB" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
if [[ "${tables:-0}" -lt 5 ]]; then
  echo "✗ restore drill: мало таблиц ($tables), rc=$rc"
  exit 1
fi

echo "→ сверка counts (prod vs drill)"
TABLES="organizations users documents subscriptions payments legal_acts act_watch_log events jobs"
FAIL=0
printf '%-22s %12s %12s %s\n' "table" "prod" "drill" "ok?"
for t in $TABLES; do
  prod="$(docker compose --env-file .env exec -T postgres \
    psql -U dok -d dok -Atc "SELECT count(*) FROM $t;" 2>/dev/null || echo ERR)"
  drill="$(docker compose --env-file .env exec -T postgres \
    psql -U dok -d "$DRILL_DB" -Atc "SELECT count(*) FROM $t;" 2>/dev/null || echo ERR)"
  # drill — снимок на момент бэкапа; допускаем prod >= drill (нарост после бэкапа)
  ok="✗"
  if [[ "$prod" != "ERR" && "$drill" != "ERR" && "$prod" -ge "$drill" ]]; then
    ok="✓"
  else
    FAIL=1
  fi
  printf '%-22s %12s %12s %s\n' "$t" "$prod" "$drill" "$ok"
done

echo "→ проверка маркера backup_ok (в volume app, W-45/G-01)"
MARKER_JSON="$(docker compose --env-file .env exec -T app \
  python -c "from pathlib import Path; p=Path('/srv/dok/files/.ops/backup_ok.json'); print(p.read_text(encoding='utf-8') if p.is_file() else '')" 2>/dev/null || true)"
if [[ -n "${MARKER_JSON}" ]]; then
  echo "  маркер: volume:/srv/dok/files/.ops/backup_ok.json"
  MARKER_JSON="$MARKER_JSON" python3 - <<'PY'
import json, os
from datetime import datetime, timezone
data = json.loads(os.environ["MARKER_JSON"])
at = datetime.fromisoformat(data["at"])
if at.tzinfo is None:
    at = at.replace(tzinfo=timezone.utc)
age_h = (datetime.now(timezone.utc) - at).total_seconds() / 3600
print(f"  at={data.get('at')} age_h={age_h:.1f} file={data.get('file')}")
if age_h > 26:
    print("  ⚠ маркер старше 26 ч — алерт backup_stale должен сработать")
f = data.get("file") or ""
print(f"  archive_exists={os.path.isfile(f)} path={f}")
PY
else
  echo "  ⚠ маркер backup_ok не найден в volume app"
  FAIL=1
fi

echo "→ очистка $DRILL_DB"
docker compose --env-file .env exec -T postgres \
  psql -U dok -d postgres -c "DROP DATABASE IF EXISTS $DRILL_DB;"

if [[ "$FAIL" -ne 0 ]]; then
  echo "✗ Restore drill завершён с расхождениями"
  exit 1
fi
echo "✓ Restore drill OK (прод не изменялся)"
