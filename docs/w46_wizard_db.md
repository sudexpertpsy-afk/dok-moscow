# W-46 фаза E — мастер и лимиты в БД

Дата: 2026-09-07.

## Сделано

- Таблицы `wizard_sessions`, `rate_counters`, `dadata_cache` (миграция `w46e1a2b3c4d`).
- `package_wizard_store`: чтение/запись в БД, TTL 24 ч; legacy `_wizard/*.json` импортируется один раз.
- Worker (`ops_loop_tick`) чистит истёкшие wizard + dadata_cache.
- DaData: суточный лимит через `rate_counters` (холодный старт из Event); кэш L1 in-process + L2 БД (TTL 10 мин).
- Логин/2FA по-прежнему на `rate_limit_hits` (T2).

## Проверка

```bash
cd web && .venv/bin/python -m pytest tests/test_w46e_wizard_rate.py tests/test_package.py -q
```

На VDS после деплоя: `alembic upgrade head` (entrypoint).
