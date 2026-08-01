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

Откройте http://127.0.0.1:8000/ (лендинг) или http://127.0.0.1:8000/login.

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
- W-05 — картотека контрагентов + DaData (кэш, лимит, HTMX)
- W-06 — журнал, сквозной поиск, настройки org и счётчики
- W-07 — развёртывание на VDS
- W-08 — лендинг dok.moscow, заявки (leads), политика ПДн, SEO
- W-09 — политика паролей, сброс по e-mail, журнал входов, retention, ASVS
- W-10 — модель биллинга (тарифы, подписки, платежи, payment_settings); см. `docs/ТЗ_биллинг.md`
- W-11 — Т-Касса: Token/Init/GetState/Charge, вебхук `/billing/webhook`, сверка и автопродление
- W-12 — кабинет «Тариф и оплата», лимиты генерации, водяной знак «Гость»
- W-13…W-15 — админка платежей, чеки, приёмка
