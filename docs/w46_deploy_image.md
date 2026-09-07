# W-46 фаза D — образ по тегу (GHCR)

Дата: 2026-09-07. Репо: `dra-v-losev-afk/desktop-tutorial`.

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

## Включение на проде (после merge в main)

1. Packages: в GitHub → Package settings → Visibility (private OK) + grant
   read для deploy-пользователя / `docker login ghcr.io`.
2. Первый успешный CI push → образ в GHCR.
3. На VDS: `docker login ghcr.io` (PAT `read:packages`).
4. `cd /srv/dok/deploy && ./deploy.sh --tag <tag-or-sha>`.
5. Прод **не** на ветках `cursor/*`.

Пока CI не гоняет main, прод остаётся на `compose build` (тот же Dockerfile).

## Критерии

- Деплой без scp исходников app; версия в `/healthz` = тег деплоя.
- Откат: `./deploy.sh --rollback`.
