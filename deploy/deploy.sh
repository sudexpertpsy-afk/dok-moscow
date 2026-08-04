#!/usr/bin/env bash
# Обновление Док.Москва одной командой: git pull → build → migrate → restart.
# Запуск: из корня репозитория или из deploy/: ./deploy/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
cd "$DEPLOY"

if [[ ! -f .env ]]; then
  echo "✗ Нет deploy/.env — скопируйте .env.example и заполните."
  exit 1
fi

# Мягкие проверки прод-конфига (не блокируют деплой).
if ! grep -qE '^SESSION_HTTPS_ONLY=(true|1|yes)$' .env 2>/dev/null; then
  echo "⚠ SESSION_HTTPS_ONLY не включён в deploy/.env — на HTTPS-проде задайте SESSION_HTTPS_ONLY=true"
fi
if grep -qE '^YANDEX_REDIRECT_URI=https://dok\.moscow/' .env 2>/dev/null; then
  echo "⚠ YANDEX_REDIRECT_URI указывает на dok.moscow — нужен https://app.dok.moscow/auth/yandex/callback"
fi

# Правило аудита (F-03): деплой только при зелёном полном pytest в CI/агенте.
# На сервере полный прогон не гоняем — ожидаем, что ветка уже проверена до push в main.

echo "→ git pull"
git -C "$ROOT" pull --ff-only

# Шаблоны DOCX в core/Шаблоны отслеживаются git'ом: checkout/pull возвращает
# файлы, удалённые через /admin/templates. Tombstone (.deleted_templates.json)
# хранит список удалений — снимаем вернувшиеся файлы до перезапуска контейнеров.
# Только stdlib: на хосте деплоя может не быть зависимостей приложения.
TEMPLATES_DIR="${TEMPLATES_DIR:-$ROOT/core/Шаблоны}"
if [[ -f "$TEMPLATES_DIR/.deleted_templates.json" ]]; then
  echo "→ scrub удалённых шаблонов (tombstone)"
  TEMPLATES_DIR="$TEMPLATES_DIR" python3 - <<'PY' || echo "⚠ не удалось применить tombstone шаблонов (продолжаем деплой)"
import json, os
from pathlib import Path

root = Path(os.environ["TEMPLATES_DIR"])
data = json.loads((root / ".deleted_templates.json").read_text(encoding="utf-8"))
deleted = [str(x) for x in (data.get("deleted") or []) if str(x).endswith(".docx")]
removed = []
for name in deleted:
    name = Path(name).name
    docx = root / name
    if docx.is_file():
        docx.unlink()
        removed.append(name)
    manifest = root / f"{Path(name).stem}.manifest.yaml"
    if manifest.is_file():
        manifest.unlink()
reg_path = root / "contracts_registry.json"
if reg_path.is_file() and deleted:
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        reg = None
    if isinstance(reg, dict):
        contracts = reg.get("contracts")
        if isinstance(contracts, dict):
            for key, vals in list(contracts.items()):
                if isinstance(vals, list):
                    contracts[key] = [x for x in vals if x not in deleted]
        if isinstance(reg.get("self_contained"), list):
            reg["self_contained"] = [x for x in reg["self_contained"] if x not in deleted]
        reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"  убрано файлов: {len(removed)}")
for name in removed:
    print(f"  - {name}")
PY
fi

echo "→ docker compose build (app + worker + ops-agent + gotenberg с fonts-liberation)"
docker compose --env-file .env build app worker ops-agent gotenberg

echo "→ docker compose up -d"
docker compose --env-file .env up -d --remove-orphans

echo "→ ожидание health postgres / миграции (entrypoint app)"
# миграции выполняет docker-entrypoint.sh при старте app
for i in $(seq 1 30); do
  if docker compose --env-file .env ps app | grep -q "Up"; then
    break
  fi
  sleep 2
done

echo "→ статус"
docker compose --env-file .env ps

echo "✓ Обновление завершено. Проверьте https://app.dok.moscow/login"
