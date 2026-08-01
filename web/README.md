# web — FastAPI-приложение Док.Москва

Стек: FastAPI + Jinja2 + HTMX, SQLAlchemy 2 + Alembic, PostgreSQL 16.

## Локальный запуск

```bash
cd web
source ../.venv/bin/activate   # или ./scripts/setup_dev.sh из корня
pip install -r requirements-dev.txt
cp .env.example .env           # SECRET_KEY, BOOTSTRAP_ADMIN_*, DB_URL
# миграции (PostgreSQL):
alembic upgrade head
uvicorn app.main:app --reload --app-dir .
```

Откройте http://127.0.0.1:8000/login.

## Тесты

```bash
cd web && PYTHONPATH=. pytest tests -q
```

Для проверки Alembic нужен PostgreSQL (см. `TEST_PG_URL` / пользователь `dok`).

## Пакеты

- W-01 — каркас, auth, инвайты
- W-02 — схема PostgreSQL + Alembic + изоляция org_id
- W-03 — генерация DOCX (ядро), счётчики FOR UPDATE, PDF через Gotenberg
- W-04 — мастер комплектов (ФЛ/ЮЛ/ГПД, ZIP/PDF, повтор)
- W-05… — контрагенты + DaData и далее по ТЗ
