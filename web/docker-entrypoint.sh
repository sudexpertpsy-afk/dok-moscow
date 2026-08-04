#!/usr/bin/env bash
# Точка входа контейнера app: миграции, затем uvicorn.
set -euo pipefail

echo "→ Ожидание PostgreSQL..."
python - <<'PY'
import os, time
from sqlalchemy import create_engine, text
url = os.environ.get("DB_URL", "")
if not url:
    raise SystemExit("DB_URL не задан")
for i in range(60):
    try:
        e = create_engine(url)
        with e.connect() as c:
            c.execute(text("SELECT 1"))
        print("✓ БД доступна")
        break
    except Exception as exc:
        time.sleep(2)
else:
    raise SystemExit("PostgreSQL недоступен")
PY

# Worker и параллельные реплики: миграции только у app (иначе гонка CREATE EXTENSION).
if [[ "${SKIP_MIGRATIONS:-0}" != "1" ]]; then
  echo "→ alembic upgrade head"
  alembic upgrade head
else
  echo "→ SKIP_MIGRATIONS=1 — alembic пропущен"
fi

# Убрать DOCX, возвращённые git pull'ом, если админ удалял их через UI (tombstone).
if [[ "${SKIP_TEMPLATE_TOMBSTONE:-0}" != "1" ]]; then
  python - <<'PY' || true
from pathlib import Path
try:
    from app.services.template_admin import apply_deleted_templates, deleted_templates_path
    if deleted_templates_path().is_file():
        removed = apply_deleted_templates()
        if removed:
            print(f"→ tombstone шаблонов: убрано {len(removed)}")
except Exception as exc:
    print(f"⚠ tombstone шаблонов: {exc}")
PY
fi

echo "→ Запуск приложения"
exec "$@"
