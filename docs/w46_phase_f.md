# W-46 фаза F — манифесты, источник шаблонов, docs

Дата: 2026-09-08.

## Манифесты

- У каждого из 34 системных DOCX в `core/Шаблоны/` есть `*.manifest.yaml`.
- 8 curated (заключения, УПД, сверка, РКО, письмо…) оставлены как есть.
- 26 черновиков сгенерированы `scripts/generate_draft_manifests.py`
  (маркер `# черновик W-46: подписи вычитать владельцу`).
- Тесты: `web/tests/test_w46f_manifests_registry.py` — нет DOCX без манифеста;
  `contracts_registry.json` не ссылается на отсутствующие файлы.

Перегенерация черновиков:

```bash
web/.venv/bin/python scripts/generate_draft_manifests.py
```

## Источник правды шаблонов

| Слой | Роль |
|------|------|
| `core/Шаблоны` в git | seed / источник правды для system |
| `/srv/dok/data/templates` на проде | рабочая копия: `system/` + `overrides/` |
| Desktop DocFiller | **legacy**: заморожен; новые сценарии — только веб |

Третьей «живой» копии шаблонов быть не должно.

## Docs

- `docs/эксплуатация.md` §2 — один канонический деплой (`deploy.sh` / `--tag`).
- `docs/шаблонер_структура_и_сервер.md` — data-пути W-46, desktop = legacy.

## Переименование репо

Цель: `desktop-tutorial` → `dok-moscow` на GitHub (редирект старых URL).

**Статус 2026-09-08:** из агента не выполнено — активный `gh` = `sudexpertpsy-afk`,
репо у `dra-v-losev-afk`. Нужен вход владельца.

Под владельцем `dra-v-losev-afk`:

```bash
# вариант A — CLI
gh auth login   # аккаунт dra-v-losev-afk
cd /path/to/repo
gh repo rename dok-moscow --yes

# вариант B — UI
# https://github.com/dra-v-losev-afk/desktop-tutorial/settings
# → Repository name → dok-moscow → Rename
```

После rename — обновить remote в клонах и на VDS:

```bash
git remote set-url origin git@github.com:dra-v-losev-afk/dok-moscow.git
# на VDS:
# git -C /srv/dok remote set-url origin git@github.com:dra-v-losev-afk/dok-moscow.git
```
