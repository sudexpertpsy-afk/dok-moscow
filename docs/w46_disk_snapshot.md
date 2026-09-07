# W-46 §3 — снимок диска VDS (2026-09-07)

Хост: `178.212.14.15` (`/srv/dok`). Снято до/после prune build-cache.

## Корневой ФС

| Момент | Used | Avail | Use% |
|--------|------|-------|------|
| До prune | 14 ГБ / 30 ГБ | 15 ГБ | **50%** |
| После `builder prune -af` + `image prune --filter until=168h` | 9.6 ГБ / 30 ГБ | 19 ГБ | **35%** |

Критерий ТЗ «после чистки ≤ 35%» — выполнен.

## Docker (`docker system df`)

| Тип | До | После |
|-----|----|-------|
| Images | 4.07 ГБ (7) | 4.07 ГБ (6, без orphan gotenberg:8) |
| Containers | ~0.8 МБ | без изменений |
| Volumes | 183.6 МБ | без изменений |
| **Build Cache** | **5.7 ГБ** (reclaimable ~4.1 ГБ) | **0 Б** |

Главный потребитель роста — слои build cache от частых `compose build` / `--no-cache` на VDS (Р-4).

### Тома

| Volume | Size |
|--------|------|
| `dok_pgdata` | 179.2 МБ |
| `dok_files` | 4.3 МБ |
| `dok_caddy_*` | < 20 КБ |

Host `/srv/dok/files` по-прежнему не равен volume (Р-2) — миграция в фазе B.

# Бэкапы `/var/backups/dok`

| | |
|--|--|
| Daily | **30** файлов (лимит ротации) |
| Monthly | **12** файлов (лимит ротации) |
| Размер | **1.3 ГБ** |
| Cron | `30 3 * * * /srv/dok/deploy/backup.sh` — по логу успешен до 2026-09-07 |
| Формат | `daily/dok_YYYY-MM-DD.tar.age` |

Ротация фактически соответствует замыслу (30/12).

## journald

- До: ~24 МБ, `SystemMaxUse` не задан.
- После: `SystemMaxUse=500M` в `/etc/systemd/journald.conf`; usage ~8 МБ.

## Расписание prune (добавлено)

```
0 5 * * * docker image prune -af --filter until=168h; docker builder prune -af
```

(crontab пользователя `deploy`)

## Алерт диска (код)

Порог в `web/app/services/ops.py`: **75%** (было 85%, W-46 §3 п.4).

## Следующие шаги (не эта фаза)

- Фаза B: bind `/srv/dok/data/files` вместо named volume.
- Фаза D: сборка образов в CI → prune на VDS почти не нужен.
