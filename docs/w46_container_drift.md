# W-46 — дрейф контейнера vs git на VDS

## Прогон §8 перед merge (2026-09-08)

Команды на VDS (`/srv/dok`, не `/srv/dok/app`):

```bash
docker compose -f deploy/compose.yml cp app:/app/app /tmp/prod_app
diff -rq --exclude=static --exclude=__pycache__ --exclude='*.pyc' \
  /tmp/prod_app/app /srv/dok/web/app
```

| Сравнение | Результат |
|-----------|-----------|
| container ↔ host `/srv/dok/web/app` (без `static`) | **пусто** — код в образе = дерево на диске |
| только `static/*.hash.*` | есть в контейнере (артефакты сборки) — **не** scp-хотфиксы |
| host ↔ ветка `cursor/w46-infra-hygiene-0030` | **1 файл:** `web/app/services/facsimile.py` |

### Найденный хотфикс (зафиксирован в ветке до merge)

Прод: `getattr(get_settings(), "faksimile_enabled", True)` вместо прямого атрибута.

**Почему не было атрибута:** scp-рассинхрон W-43 — в контейнер попал `facsimile.py`
с обращением к полю раньше, чем `config.py` с `faksimile_enabled` (или без
`get_settings.cache_clear` после подмены модуля). Не «старая запись БД» и не
миграция: поле только в pydantic Settings.

**Почему True:** это kill-switch инцидента (`FAKSIMILE_ENABLED`), дефолт модели
и `.env.example` тоже `True` («фича в норме включена»). Разрешительная семантика
W-43 — в `FacsimilePolicy` по шаблону; getattr не обходит запреты шаблонов.

### Состояние git на VDS (важно)

- Ветка: `cursor/landing-bounce-reduction-0030` @ `643eca8`
- Working tree **грязный**: незакоммиченные правки W-46 (B–E и др.) уже на диске
  и в работающем образе (`/healthz` = `w46e-20260907`)
- После cutover на образ working tree не источник правды; переключить checkout
  на `main` / оставить только `deploy/`

## Снимок 2026-09-07

Снято: `2026-09-07T18:58:38Z` скриптом [`scripts/w46_container_vs_git_diff.sh`](../scripts/w46_container_vs_git_diff.sh).
Тогда `app`/`core` совпадали с host; фаза B ещё не была.

## Повтор

```bash
./scripts/w46_container_vs_git_diff.sh
# и обязательно: host (или container) ↔ ветка PR, не только container ↔ host
```
