# W-46 фаза C — шаблоны как данные

Дата: 2026-09-07. Хост: `deploy@178.212.14.15`, `/srv/dok`.

## Раскладка

```
/srv/dok/data/templates/          # TEMPLATES_DIR (bind host == container)
  system/                         # seed из core/Шаблоны (rsync --delete при деплое)
  overrides/                      # правки админки (deploy не трогает)
  .deleted_templates.json         # tombstone кабинета
  contracts_registry.json         # рабочий реестр комплекта (корень)
```

## Что сделано на VDS

- `rsync` установлен (apt).
- `migrate_templates_data.sh`: seed → `system/`, 34 DOCX; `layered=True`; `list_templates()` → 25 (минус tombstone).
- `compose.yml`: `TEMPLATES_DIR=/srv/dok/data/templates`, bind того же пути; RW-bind `core/Шаблоны` снят.
- `deploy.sh` синкает seed → `system/` перед up.
- Админка пишет только в `overrides/`; экспорт zip: `GET /admin/templates/export-overrides`.

## Критерии

- Повторный деплой не затирает overrides.
- Удаление через админку: override снимается с диска, system остаётся под tombstone (витрина).
- Restore drill (фаза B) уже покрывает `data/` целиком.

Следующая фаза: D — образ по тегу (GHCR) + `deploy.sh` pull.
