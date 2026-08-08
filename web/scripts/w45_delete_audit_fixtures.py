#!/usr/bin/env python3
"""W-45: удалить фикстуры АУДИТ-* (org с маркером / именем АУДИТ-).

  python scripts/w45_delete_audit_fixtures.py --dry-run
  python scripts/w45_delete_audit_fixtures.py --yes
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _is_audit_org(org) -> bool:
    name = (org.name or "").strip()
    if name.startswith("АУДИТ-"):
        return True
    req = org.requisites or {}
    if isinstance(req, dict):
        marker = req.get("_audit") or {}
        if isinstance(marker, dict) and marker.get("delete_after_audit"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.yes:
        print("Укажите --dry-run или --yes", file=sys.stderr)
        return 2

    from sqlalchemy import delete, select

    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import (
        CalendarEvent,
        Contract,
        Counter,
        Counterparty,
        Document,
        Event,
        Invite,
        Job,
        LawBookmark,
        LawNote,
        LawView,
        LawWatch,
        LawWatchNotice,
        Organization,
        OrgField,
        PartyCheck,
        Payment,
        Subscription,
        User,
    )

    settings = get_settings()
    files_root = Path(settings.files_root)
    db = SessionLocal()
    try:
        orgs = [o for o in db.scalars(select(Organization)).all() if _is_audit_org(o)]
        if not orgs:
            print("Нет АУДИТ-организаций")
            return 0
        org_ids = [o.id for o in orgs]
        print("orgs:", [(o.id, o.name) for o in orgs])
        users = db.scalars(select(User).where(User.org_id.in_(org_ids))).all()
        user_ids = [u.id for u in users]
        print("users:", [(u.id, u.email) for u in users])
        if args.dry_run:
            return 0

        for model in (
            LawWatchNotice,
            LawWatch,
            LawNote,
            LawBookmark,
            LawView,
            CalendarEvent,
            PartyCheck,
            Document,
            Job,
            Counterparty,
            Contract,
            OrgField,
            Counter,
            Event,
            Invite,
        ):
            col = getattr(model, "org_id", None)
            if col is None:
                continue
            n = db.execute(delete(model).where(model.org_id.in_(org_ids))).rowcount
            print(f"delete {model.__tablename__}: {n}")
        n = db.execute(delete(Payment).where(Payment.org_id.in_(org_ids))).rowcount
        print(f"delete payments: {n}")
        n = db.execute(delete(Subscription).where(Subscription.org_id.in_(org_ids))).rowcount
        print(f"delete subscriptions: {n}")
        if user_ids:
            from app.models import OAuthIdentity, PasswordResetToken

            db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id.in_(user_ids)))
            db.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id.in_(user_ids)))
        n = db.execute(delete(User).where(User.org_id.in_(org_ids))).rowcount
        print(f"delete users: {n}")
        n = db.execute(delete(Organization).where(Organization.id.in_(org_ids))).rowcount
        print(f"delete organizations: {n}")
        db.commit()
        for oid in org_ids:
            org_dir = files_root / str(oid)
            if org_dir.is_dir():
                shutil.rmtree(org_dir)
                print(f"removed files {org_dir}")
        print("OK")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
