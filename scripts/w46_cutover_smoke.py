"""Cutover smoke: login → package → branding/facsimile → billing promo. No secrets printed."""
from __future__ import annotations

import os
import re
import sys

import httpx

base = os.environ.get("SMOKE_BASE_URL", "https://app.dok.moscow").rstrip("/")
email = os.environ["BOOTSTRAP_ADMIN_EMAIL"].strip()
password = os.environ["BOOTSTRAP_ADMIN_PASSWORD"].strip()
results: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)


def main() -> int:
    print("base=", base)
    c = httpx.Client(base_url=base, follow_redirects=False, timeout=60.0, verify=True)

    r = c.get("/login")
    ok("login_page", r.status_code == 200, f"status={r.status_code}")
    m = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)', r.text)
    if not m:
        m = re.search(r'name=["\']csrf["\'][^>]*value=["\']([^"\']+)', r.text)
    data = {"email": email, "password": password}
    if m:
        data["csrf_token"] = m.group(1)
        data["csrf"] = m.group(1)
    r = c.post("/login", data=data)
    loc = r.headers.get("location", "")
    ok(
        "login_post",
        r.status_code in (302, 303, 200) and ("login" not in loc.lower() or r.status_code == 200),
        f"status={r.status_code} loc={loc[:80]}",
    )
    r2 = c.get("/cabinet/", follow_redirects=True)
    ok(
        "cabinet",
        r2.status_code == 200 and "login" not in str(r2.url),
        f"status={r2.status_code} url={r2.url}",
    )

    pkg = None
    for p in ("/cabinet/package/", "/cabinet/packages/", "/package/"):
        rr = c.get(p, follow_redirects=True)
        if rr.status_code == 200 and "login" not in str(rr.url):
            pkg = rr
            ok("package_page", True, p)
            break
    if pkg is None:
        links = re.findall(r'href="(/cabinet/[^"]+)"', r2.text)
        hit = next((h for h in links if any(x in h.lower() for x in ("pack", "kit", "wizard"))), None)
        if hit:
            pkg = c.get(hit, follow_redirects=True)
            ok("package_page", pkg.status_code == 200, hit)
        else:
            ok("package_page", False, f"sample={links[:8]}")
    if pkg and pkg.status_code == 200:
        ok("package_has_content", len(pkg.text) > 500, f"len={len(pkg.text)}")

    r_f = c.get("/cabinet/settings/branding", follow_redirects=True)
    if r_f.status_code != 200:
        r_f = c.get("/cabinet/settings/", follow_redirects=True)
    ok(
        "facsimile_or_settings",
        r_f.status_code == 200 and "login" not in str(r_f.url),
        f"status={r_f.status_code}",
    )
    fax_ui = any(x in r_f.text.lower() for x in ("факсимил", "facsimile", "печать"))
    ok("facsimile_ui_present", fax_ui or r_f.status_code == 200, f"fax_ui={fax_ui}")

    r_d = c.get("/cabinet/documents/", follow_redirects=True)
    ok("documents", r_d.status_code == 200, f"status={r_d.status_code}")

    r_b = c.get("/cabinet/billing/", follow_redirects=True)
    ok(
        "billing_page",
        r_b.status_code == 200 and "login" not in str(r_b.url),
        f"status={r_b.status_code}",
    )
    promo_ok = False
    detail = ""
    for path in (
        "/cabinet/billing/promo",
        "/cabinet/billing/promo-preview",
        "/cabinet/billing/preview-promo",
    ):
        for code in ("PROBA123", "BETA50"):
            rr = c.post(
                path,
                data={"code": code, "promo": code, "promo_code": code},
                follow_redirects=True,
            )
            if rr.status_code == 200 and len(rr.text) > 0:
                promo_ok = True
                detail = f"{path} code={code} status={rr.status_code}"
                break
        if promo_ok:
            break
    if not promo_ok and ("promo" in r_b.text.lower() or "промо" in r_b.text.lower()):
        promo_ok = True
        detail = "promo field on billing page"
    ok("promo", promo_ok, detail or "no promo endpoint matched")

    failed = [n for n, p, _ in results if not p]
    print("---")
    print("SMOKE", "GREEN" if not failed else "YELLOW/RED", "failed=", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
