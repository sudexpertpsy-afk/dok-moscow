#!/usr/bin/env bash
# W-46 фаза C: seed core/Шаблоны → /srv/dok/data/templates/{system,overrides}
# Не останавливает сервис дольше обычного recreate. Запуск на VDS.
set -euo pipefail

ROOT="${DOK_ROOT:-/srv/dok}"
SEED="$ROOT/core/Шаблоны"
DATA="$ROOT/data/templates"
DEPLOY="$ROOT/deploy"

mkdir -p "$DATA/system" "$DATA/overrides"
# перенести tombstone из старого flat-bind, если был
if [[ -f "$SEED/.deleted_templates.json" && ! -f "$DATA/.deleted_templates.json" ]]; then
  cp -a "$SEED/.deleted_templates.json" "$DATA/.deleted_templates.json"
fi

if ! command -v rsync >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq rsync
fi

echo "→ rsync $SEED → $DATA/system"
rsync -a --delete --exclude '.deleted_templates.json' "$SEED/" "$DATA/system/"
if [[ ! -f "$DATA/contracts_registry.json" ]]; then
  cp -a "$DATA/system/contracts_registry.json" "$DATA/contracts_registry.json"
fi
chown -R deploy:deploy "$DATA" 2>/dev/null || sudo chown -R deploy:deploy "$DATA"

echo "→ counts system=$(find "$DATA/system" -name '*.docx' | wc -l) overrides=$(find "$DATA/overrides" -name '*.docx' | wc -l)"

cd "$DEPLOY"
# compose уже должен указывать TEMPLATES_DIR=/srv/dok/data/templates
docker compose --env-file .env up -d --no-deps app worker
sleep 3
docker compose --env-file .env exec -T app python -c "
from app.services.templates import templates_dir, is_layered_templates, list_templates, system_templates_dir
print('TEMPLATES_DIR', templates_dir())
print('layered', is_layered_templates())
print('system', system_templates_dir())
print('count', len(list_templates()))
"
echo "✓ templates seed OK"
