# W-46 фаза D — образ по тегу (GHCR)

Дата: 2026-09-07. Репо: [`sudexpertpsy-afk/dok-moscow`](https://github.com/sudexpertpsy-afk/dok-moscow).

## Что в коде

- `web/Dockerfile` — контекст **корень репо**, `COPY core`, `ARG/ENV APP_VERSION`.
- `.github/workflows/publish-dok-app.yml` — push в `main` / тег `v*` →
  `ghcr.io/<owner>/dok-app:<sha|tag|latest>`.
- `deploy/compose.yml` — `image: ${DOK_IMAGE:-dok-app:local}` + build с корня;
  bind `../core` снят (ядро в образе).
- `deploy/deploy.sh`:
  - без флагов / без `DOK_IMAGE` — legacy: git pull + `compose build`;
  - `--tag vX.Y.Z` — `compose pull` + seed + up + smoke `/healthz` + prune 168h;
  - `--rollback` — предпоследний `tag=` из `/srv/dok/data/ops/deploy.log`.
- `/healthz` → `{ok, version}`; `/admin/status` показывает `APP_VERSION` и дрейф
  относительно `data/ops/deployed_version`.

## Cutover (дополненный порядок)

Сейчас на проде почти нет живых данных (files ~4 МБ, wizard/dadata пусты) —
самый дешёвый момент для первого деплоя образом. Не откладывать «до стабилизации».

0. **§8 дрейф** — container↔host (код) пуст; host↔ветка PR пуст (см. `w46_container_drift.md`).
1. **`git log ветка..main` пуст** — иначе merge потеряет коммиты main.
2. **Merge PR #1 merge-коммитом** (не squash) → тег **`v1.0.0`**.
3. Actions собирает образ по тегу; в образе `APP_VERSION=v1.0.0`.
4. **До pull на VDS:** репо новый (`sudexpertpsy-afk/dok-moscow`, не transfer).
   Fine-grained PAT с **только** `read:packages` → `GHCR_TOKEN` в `deploy/.env`
   (не в history shell) → `docker login ghcr.io`.
5. `./deploy.sh --tag v1.0.0`. После cutover без `DOK_IMAGE`/`--tag` скрипт должен
   **отказывать** (иначе снова соберут на сервере).
6. `/healthz` = `v1.0.0`; `/admin/status` = тег; искусственно сбить ожидаемый
   тег → жёлтый дрейф + письмо.
7. Smoke (~4 мин): вход, комплект ФЛ, PDF с факсимиле, оплата тестовым промокодом.
8. VDS: убрать checkout `cursor/*`; дерево нужно только для `deploy/` (или `main`).
9. `dok_files`: удалить **не раньше 2026-09-14** (B = 2026-09-07 + 7 дней).

Пока CI не гоняет main, прод остаётся на `compose build` (тот же Dockerfile).

## Критерии

- Деплой без scp исходников app; версия в `/healthz` = тег деплоя.
- Откат: `./deploy.sh --rollback`.
