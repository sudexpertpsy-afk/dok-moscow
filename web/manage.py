#!/usr/bin/env python3
"""CLI управления Док.Москва (W-50.1).

Примеры:
  cd web && PYTHONPATH=. python manage.py mark_internal 1,2,3
  cd web && PYTHONPATH=. python manage.py mark_internal 1 2 3

Опционально: INTERNAL_ORG_IDS=1,2,3 — если аргументы не переданы, берутся из env.
"""

from __future__ import annotations

import argparse
import os
import sys


def _parse_ids(raw_parts: list[str]) -> list[int]:
    ids: list[int] = []
    for part in raw_parts:
        for chunk in str(part).replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            ids.append(int(chunk))
    # уникальные с сохранением порядка
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def cmd_mark_internal(ids: list[int]) -> int:
    from app.db import SessionLocal
    from app.models import Organization

    if not ids:
        print("нет id для пометки", file=sys.stderr)
        return 1

    marked: list[int] = []
    db = SessionLocal()
    try:
        for oid in ids:
            org = db.get(Organization, oid)
            if org is None:
                print(f"предупреждение: организация id={oid} не найдена", file=sys.stderr)
                continue
            if not org.is_internal:
                org.is_internal = True
            marked.append(oid)
        db.commit()
    finally:
        db.close()

    print("помечено: " + (", ".join(str(i) for i in marked) if marked else "—"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Док.Москва manage CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mark = sub.add_parser(
        "mark_internal",
        help="Пометить организации is_internal=True (идемпотентно)",
    )
    p_mark.add_argument(
        "ids",
        nargs="*",
        help="ID через пробел или запятую; иначе INTERNAL_ORG_IDS",
    )

    args = parser.parse_args(argv)
    if args.command == "mark_internal":
        raw = list(args.ids or [])
        if not raw:
            env_raw = (os.environ.get("INTERNAL_ORG_IDS") or "").strip()
            if env_raw:
                raw = [env_raw]
        try:
            ids = _parse_ids(raw)
        except ValueError:
            print("некорректный id (ожидаются целые числа)", file=sys.stderr)
            return 1
        return cmd_mark_internal(ids)

    parser.error(f"неизвестная команда: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
