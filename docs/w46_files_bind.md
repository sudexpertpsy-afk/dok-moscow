# W-46 фаза B — FILES_ROOT bind

Дата: 2026-09-07. Риск Р-2 закрыт: host-путь == путь в контейнере.

## Было → стало

| | Было | Стало |
|--|------|-------|
| Путь в контейнере | `/srv/dok/files` | `/srv/dok/data/files` |
| Хранение на хосте | named volume `dok_files` | bind `/srv/dok/data/files` |
| backup маркер | через `docker exec` в volume | файл на хосте `$FILES_ROOT/.ops/backup_ok.json` |

## Миграция

Скрипт: [`deploy/migrate_files_bind.sh`](../deploy/migrate_files_bind.sh)

1. stop app/worker/ops-agent  
2. `docker run alpine cp -a` из `dok_files` → `/srv/dok/data/files`  
3. сверка counts (41 = 41)  
4. `compose up` с новым bind  

Volume **`dok_files` не удалён** — держать ≥ 7 дней от даты миграции B (**2026-09-07**),
удалять не раньше **2026-09-14**: `docker volume rm dok_files`.

## Откат

1. Вернуть в `compose.yml` volume `files:` и `FILES_ROOT=/srv/dok/files`  
2. `docker compose up -d`  
3. Данные в старом volume не трогались при копировании  

## Проверка

- mounts: `/srv/dok/data/files -> /srv/dok/data/files (bind)`  
- `get_settings().files_root` → `/srv/dok/data/files`  
- host file count == container file count  

Следующая фаза: C — шаблоны в `/srv/dok/data/templates`.
