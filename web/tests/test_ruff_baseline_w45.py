"""W-45/G-05: новый код не раздувает ruff baseline."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "docs" / "ruff_baseline_w45.txt"


def _load_baseline() -> Counter[str]:
    c: Counter[str] = Counter()
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        path, code, n = parts
        c[f"{path}\t{code}"] += int(n)
    return c


def _current() -> Counter[str]:
    ruff = Path(sys.executable).with_name("ruff")
    if not ruff.is_file():
        ruff = Path("ruff")
    proc = subprocess.run(
        [str(ruff), "check", "web/app", "core", "--output-format=concise", "--no-cache"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    pat = re.compile(r"^(.*?):(\d+):(\d+):\s+([A-Z0-9]+)\b")
    c: Counter[str] = Counter()
    for line in (proc.stdout + proc.stderr).splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        m = pat.match(line)
        if not m:
            continue
        path = m.group(1).replace(str(REPO) + "/", "").replace("/workspace/", "")
        if path.startswith("app/"):
            path = "web/" + path
        c[f"{path}\t{m.group(4)}"] += 1
    return c


@pytest.mark.skipif(not BASELINE.is_file(), reason="нет ruff baseline")
def test_ruff_baseline_not_grown():
    base = _load_baseline()
    cur = _current()
    assert base, "пустой baseline"
    new_pairs = sorted(set(cur) - set(base))
    grown = sorted(
        f"{k}: {base[k]}→{cur[k]}" for k in set(base) & set(cur) if cur[k] > base[k]
    )
    # допускаем уменьшение и исчезновение; рост/новые пары — fail
    assert not new_pairs and not grown, (
        "ruff baseline вырос (обновите docs/ruff_baseline_w45.txt осознанно):\n"
        + "\n".join([*(f"+ {p}" for p in new_pairs[:40]), *grown[:40]])
    )
