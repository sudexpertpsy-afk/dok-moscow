"""CLI: удаление просроченных файлов документов.

Запуск в контейнере app:
  python -m app.scripts.purge_retention [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.db import SessionLocal
from app.services.retention import purge_expired_documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Очистка файлов по сроку хранения")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только подсчёт, без удаления",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    db = SessionLocal()
    try:
        stats = purge_expired_documents(
            db,
            settings.files_root,
            dry_run=args.dry_run,
        )
    finally:
        db.close()
    mode = "dry-run" if args.dry_run else "purge"
    print(
        f"[{mode}] orgs={stats.orgs} files={stats.deleted_files} "
        f"rows={stats.deleted_rows} skipped={stats.skipped} errors={stats.errors}"
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
