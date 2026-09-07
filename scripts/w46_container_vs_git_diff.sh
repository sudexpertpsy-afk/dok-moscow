#!/usr/bin/env bash
# W-46: diff файлов в работающем контейнере app vs git working tree на VDS.
# Нужен ПЕРЕД переходом на образ по тегу (фаза D): иначе «чистый» деплой
# откатит scp-хотфиксы.
#
# С машины разработчика:
#   ./scripts/w46_container_vs_git_diff.sh
#
# На сервере (без SSH):
#   DOK_SSH= ./scripts/w46_container_vs_git_diff.sh
#
# Переменные: DOK_SSH (default deploy@178.212.14.15), DOK_ROOT (/srv/dok),
# COMPOSE_APP (dok-app-1), OUT_DIR (/tmp/w46-drift-$$)
set -euo pipefail

DOK_SSH="${DOK_SSH-deploy@178.212.14.15}"
DOK_ROOT="${DOK_ROOT:-/srv/dok}"
COMPOSE_APP="${COMPOSE_APP:-dok-app-1}"
OUT_DIR="${OUT_DIR:-/tmp/w46-drift-$$}"
mkdir -p "$OUT_DIR"

echo "→ контейнер $COMPOSE_APP → $OUT_DIR"

if [[ -n "$DOK_SSH" ]]; then
  ssh -o BatchMode=yes "$DOK_SSH" bash -s <<EOF
set -euo pipefail
rm -rf /tmp/w46-c-app /tmp/w46-c-core
docker cp ${COMPOSE_APP}:/app/app /tmp/w46-c-app
docker cp ${COMPOSE_APP}:/app/core /tmp/w46-c-core
tar -C ${DOK_ROOT}/web -czf /tmp/w46-host-app.tgz app
tar -C ${DOK_ROOT} -czf /tmp/w46-host-core.tgz core
tar -C /tmp -czf /tmp/w46-container.tgz w46-c-app w46-c-core
EOF
  scp -o BatchMode=yes \
    "${DOK_SSH}:/tmp/w46-container.tgz" \
    "${DOK_SSH}:/tmp/w46-host-app.tgz" \
    "${DOK_SSH}:/tmp/w46-host-core.tgz" \
    "$OUT_DIR/"
  mkdir -p "$OUT_DIR/container" "$OUT_DIR/host"
  tar -C "$OUT_DIR/container" -xzf "$OUT_DIR/w46-container.tgz"
  mv "$OUT_DIR/container/w46-c-app" "$OUT_DIR/container/app"
  mv "$OUT_DIR/container/w46-c-core" "$OUT_DIR/container/core"
  tar -C "$OUT_DIR/host" -xzf "$OUT_DIR/w46-host-app.tgz"
  tar -C "$OUT_DIR/host" -xzf "$OUT_DIR/w46-host-core.tgz"
else
  mkdir -p "$OUT_DIR/container" "$OUT_DIR/host"
  docker cp "${COMPOSE_APP}:/app/app" "$OUT_DIR/container/app"
  docker cp "${COMPOSE_APP}:/app/core" "$OUT_DIR/container/core"
  cp -a "${DOK_ROOT}/web/app" "$OUT_DIR/host/app"
  cp -a "${DOK_ROOT}/core" "$OUT_DIR/host/core"
fi

REPORT="$OUT_DIR/REPORT.txt"
{
  echo "W-46 container vs host git tree"
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "container: $COMPOSE_APP"
  echo "host_root: $DOK_ROOT"
  echo
  echo "=== app (.py/.html; без __pycache__ и static) ==="
  diff -rq \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='static' \
    "$OUT_DIR/container/app" "$OUT_DIR/host/app" 2>&1 || true
  echo
  echo "=== core (без каталога Шаблоны) ==="
  diff -rq \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='Шаблоны' \
    "$OUT_DIR/container/core" "$OUT_DIR/host/core" 2>&1 || true
  echo
  echo "=== Шаблоны: diff имён файлов ==="
  (cd "$OUT_DIR/container/core/Шаблоны" 2>/dev/null && ls -1 | sort) > "$OUT_DIR/tpl-container.txt" || true
  (cd "$OUT_DIR/host/core/Шаблоны" 2>/dev/null && ls -1 | sort) > "$OUT_DIR/tpl-host.txt" || true
  diff -u "$OUT_DIR/tpl-host.txt" "$OUT_DIR/tpl-container.txt" || true
} | tee "$REPORT"

# Краткая сводка в docs-friendly файл рядом
SUMMARY="$OUT_DIR/SUMMARY.md"
{
  echo "# W-46 drift: container vs host"
  echo
  echo "- date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- report: \`$REPORT\`"
  echo
  if diff -rq --exclude='__pycache__' --exclude='*.pyc' --exclude='static' \
    "$OUT_DIR/container/app" "$OUT_DIR/host/app" >/dev/null 2>&1; then
    echo "- **app:** совпадает с \`${DOK_ROOT}/web/app\`"
    APP_OK=1
  else
    echo "- **app:** есть расхождения (см. REPORT) — внести в git перед фазой D"
    APP_OK=0
  fi
} | tee "$SUMMARY"

echo
echo "Отчёт: $REPORT"
echo "Summary: $SUMMARY"

if [[ "${APP_OK:-0}" -eq 1 ]]; then
  echo "✓ app: контейнер совпадает с host web/app"
  exit 0
fi
echo "⚠ app: есть расхождения — внести в git перед фазой D"
exit 1
