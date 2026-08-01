#!/usr/bin/env python3
"""Первичное наполнение НПА из ИПС → черновики (публикация в /admin/legal/).

Пример:
  cd web && PYTHONPATH=. python scripts/bootstrap_legal_texts.py
  PYTHONPATH=. python scripts/bootstrap_legal_texts.py --limit 5 --replace-draft
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Подтянуть тексты ИПС в черновики")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--all-with-nd",
        action="store_true",
        help="Не только без published, а все с ips_nd (осторожно)",
    )
    parser.add_argument("--replace-draft", action="store_true")
    args = parser.parse_args()

    from app import db as dbmod
    from app.db import Base
    from app.services.legal_bootstrap import bootstrap_missing
    from app.services.legal_registry import ensure_legal_registry

    Base.metadata.create_all(bind=dbmod.engine)
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        db.commit()
        report = bootstrap_missing(
            db,
            only_without_published=not args.all_with_nd,
            limit=args.limit,
            replace_draft=args.replace_draft,
        )
        db.commit()
    finally:
        db.close()

    for r in report.results:
        if r.ok:
            print(f"OK  {r.slug}: draft=#{r.draft_id} fragments={r.fragments_filled}")
        elif r.skipped:
            print(f"SKIP {r.slug}: {r.skipped}")
        else:
            print(f"ERR {r.slug}: {r.error}")
    print(
        f"\nИтого: ok={report.ok_count} fail={report.fail_count} skip={report.skip_count}"
    )
    print("Дальше: /admin/legal/ → проверить diff → Опубликовать.")
    return 0 if report.fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
