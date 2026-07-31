# web — FastAPI-приложение Док.Москва

Стек: FastAPI + Jinja2 + HTMX, SQLAlchemy 2, сессии в cookie, bcrypt.

## Локальный запуск (W-01)

```bash
cd web
python3.12 -m venv ../.venv   # или используйте корневой .venv
source ../.venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # задайте SECRET_KEY и BOOTSTRAP_ADMIN_*
uvicorn app.main:app --reload --app-dir .
```

Откройте http://127.0.0.1:8000/login — войдите как bootstrap-админ,
создайте организацию и приглашение.

## Тесты

```bash
cd web && PYTHONPATH=. pytest tests -q
```

## Пакеты

- W-01 — каркас, auth, инвайты (этот каталог)
- W-02… — схема PostgreSQL/Alembic, генерация документов и далее по ТЗ
