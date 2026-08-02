#!/usr/bin/env bash
# Белый сценарий redeploy для ops-agent (W-34).
# Вызов: ./deploy/ops_redeploy.sh <ref>
set -euo pipefail

REF="${1:-main}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! "$REF" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "✗ Некорректный ref: $REF" >&2
  exit 1
fi

echo "→ git fetch"
git fetch --tags --force origin

echo "→ checkout $REF"
if git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
  git checkout -B "$REF" "origin/$REF"
elif git show-ref --verify --quiet "refs/tags/$REF"; then
  git checkout --force "tags/$REF"
else
  git checkout --force "$REF"
fi

echo "→ deploy.sh"
./deploy/deploy.sh
