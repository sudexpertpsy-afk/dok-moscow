#!/usr/bin/env bash
# Локальная / облачная среда разработки dok-moscow (ядро + pytest).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.12}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

echo "→ Python: $($PYTHON --version) ($($PYTHON -c 'import sys; print(sys.executable)'))"
echo "→ Создаю .venv в корне репозитория ..."
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -r core/requirements-dev.txt

echo "→ Прогон тестов ядра ..."
(cd core && python -m pytest tests -q)

echo
echo "✓ Окружение готово."
echo "  Активация:  source .venv/bin/activate"
echo "  Тесты:      cd core && pytest tests -q"
echo "  Далее:      пакеты W-01… по docs/ТЗ_веб_MVP.md"
