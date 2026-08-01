#!/usr/bin/env python3
"""Хэширование имён статики → manifest.json (W-31). Без Node."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "static"
HASH_NAMES = (
    "app.css",
    "landing.css",
    "global-search.js",
    "htmx-config.js",
    "nav-sortable.js",
    "htmx.min.js",
)


def main() -> int:
    if not ROOT.is_dir():
        print(f"нет каталога {ROOT}", file=sys.stderr)
        return 1
    manifest: dict[str, str] = {}
    for name in HASH_NAMES:
        src = ROOT / name
        if not src.is_file():
            continue
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:10]
        stem, suf = src.stem, src.suffix
        hashed = f"{stem}.{digest}{suf}"
        dst = ROOT / hashed
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copy2(src, dst)
        manifest[name] = hashed
        print(f"{name} → {hashed}")
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"manifest: {len(manifest)} файлов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
