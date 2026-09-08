#!/usr/bin/env python3
"""W-45/Б-3: PageSpeed Insights для публичных URL.

  PAGESPEED_API_KEY=... python scripts/w45_pagespeed.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

URLS = (
    "https://dok.moscow/",
    "https://dok.moscow/tariffs",
    "https://dok.moscow/zakon/",
    "https://dok.moscow/obraztsy",
)
GOAL = 90


def fetch(url: str, key: str) -> dict:
    q = urllib.parse.urlencode(
        {
            "url": url,
            "key": key,
            "strategy": "mobile",
            "category": ["performance", "accessibility", "best-practices", "seo"],
        },
        doseq=True,
    )
    api = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?{q}"
    with urllib.request.urlopen(api, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    key = (os.environ.get("PAGESPEED_API_KEY") or "").strip()
    if not key:
        print("PAGESPEED_API_KEY не задан", file=sys.stderr)
        return 2
    rows = []
    for url in URLS:
        data = fetch(url, key)
        cats = data.get("lighthouseResult", {}).get("categories", {})
        audits = data.get("lighthouseResult", {}).get("audits", {})
        perf = (cats.get("performance") or {}).get("score")
        score = int(round((perf or 0) * 100))
        lcp = (audits.get("largest-contentful-paint") or {}).get("displayValue")
        cls = (audits.get("cumulative-layout-shift") or {}).get("displayValue")
        rows.append({"url": url, "score": score, "lcp": lcp, "cls": cls, "ok": score >= GOAL})
        print(f"{url}\tscore={score}\tLCP={lcp}\tCLS={cls}")
    out = Path("docs/pagespeed_w45.md")
    lines = [
        "# W-45 PageSpeed (mobile)",
        "",
        f"Цель: performance ≥ {GOAL}.",
        "",
        "| URL | Score | LCP | CLS | OK |",
        "|-----|------:|-----|-----|:--:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['url']}` | {r['score']} | {r['lcp']} | {r['cls']} | "
            f"{'✅' if r['ok'] else '❌'} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
