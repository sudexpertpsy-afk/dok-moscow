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

echo "→ alembic upgrade head"
alembic upgrade head

echo "→ Запуск приложения"
exec "$@"
