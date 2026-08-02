#!/usr/bin/env python3
"""Пакетная публикация черновиков НПА на /zakon.

Пример:
  cd web && PYTHONPATH=. python scripts/publish_legal_drafts.py
  PYTHONPATH=. python scripts/publish_legal_drafts.py --all-drafts
  PYTHONPATH=. python scripts/publish_legal_drafts.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Опубликовать непустые черновики НПА (осознанное действие)"
    )
    parser.add_argument(
        "--all-drafts",
        action="store_true",
        help="Включая акты, у которых уже есть published (заменить редакцию)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет опубликовано",
    )
    parser.add_argument("--min-chars", type=int, default=40)
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="reviewed_by_user_id (по умолчанию — bootstrap admin, если есть)",
    )
    args = parser.parse_args()

    from sqlalchemy import select

    from app import db as dbmod
    from app.models import User, UserRole
    from app.services.legal_admin import publish_all_drafts

    db = dbmod.SessionLocal()
    try:
        user_id = args.user_id
        if user_id is None:
            admin = db.scalar(
                select(User).where(User.role == UserRole.service_admin).limit(1)
            )
            user_id = admin.id if admin else None

        if args.dry_run:
            from app.models import ActVersion, ActVersionStatus, LegalAct
            import re

            acts = db.scalars(
                select(LegalAct).order_by(LegalAct.sort_order, LegalAct.id)
            ).all()
            n = 0
            for act in acts:
                draft = db.scalar(
                    select(ActVersion)
                    .where(
                        ActVersion.act_id == act.id,
                        ActVersion.status == ActVersionStatus.draft,
                    )
                    .order_by(ActVersion.id.desc())
                )
                if draft is None:
                    continue
                plain = re.sub(r"<[^>]+>", " ", draft.body_html or "")
                plain = re.sub(r"\s+", " ", plain).strip()
                print(f"WOULD {act.slug}: draft=#{draft.id} chars={len(plain)}")
                n += 1
            print(f"\nDry-run: кандидатов {n}")
            return 0

        report = publish_all_drafts(
            db,
            user_id=user_id,
            min_body_chars=args.min_chars,
            only_without_published=not args.all_drafts,
        )
        db.commit()
    finally:
        db.close()

    for i in report.items:
        if i.ok:
            print(f"OK   {i.slug}: version=#{i.version_id}")
        elif i.skipped:
            print(f"SKIP {i.slug}: {i.skipped}")
        else:
            print(f"ERR  {i.slug}: {i.error}")
    print(
        f"\nИтого: published={report.ok_count} skip={report.skip_count} fail={report.fail_count}"
    )
    return 0 if report.fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
