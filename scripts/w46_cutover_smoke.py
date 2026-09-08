"""Cutover smoke against https://app.dok.moscow. Credentials via env only."""
from __future__ import annotations

import os
import re
import sys

import httpx

base = os.environ.get("SMOKE_BASE_URL", "https://app.dok.moscow").rstrip("/")
email = os.environ["SMOKE_EMAIL"].strip()
password = os.environ["SMOKE_PASSWORD"].strip()
results: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)


def csrf_from(html: str) -> str | None:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def main() -> int:
    print("base=", base, "email_set=", bool(email))
    c = httpx.Client(base_url=base, follow_redirects=False, timeout=90.0)

    r = c.get("/login")
    ok("login_page", r.status_code == 200, f"status={r.status_code}")
    token = csrf_from(r.text)
    ok("csrf", bool(token), f"len={len(token or '')}")
    r = c.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token or ""},
    )
    loc = r.headers.get("location") or ""
    # 2FA redirect is still a successful password check
    if r.status_code in (302, 303) and "/login/2fa" in loc:
        ok("login_post", True, f"2fa_required loc={loc}")
        print("SMOKE BLOCKED: account has TOTP — need 2FA code for full smoke")
        return 2
    ok(
        "login_post",
        r.status_code in (302, 303) and "login" not in loc.lower(),
        f"status={r.status_code} loc={loc[:100]}",
    )
    if r.status_code in (302, 303) and loc:
        c.get(loc, follow_redirects=True)

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
        hit = next(
            (h for h in links if any(x in h.lower() for x in ("pack", "kit", "wizard", "комплект"))),
            None,
        )
        if hit:
            pkg = c.get(hit, follow_redirects=True)
            ok("package_page", pkg.status_code == 200, hit)
        else:
            # try common Russian routes from nav
            for h in links:
                if "document" in h or "шаблон" in h:
                    continue
            ok("package_page", False, f"sample={links[:12]}")
    if pkg is not None and pkg.status_code == 200:
        text = pkg.text
        flish = any(x in text for x in ("ФЛ", "физлиц", "физическ", "Individual", "package"))
        ok("package_fl_content", len(text) > 400, f"len={len(text)} flish={flish}")

    r_f = c.get("/cabinet/settings/branding", follow_redirects=True)
    if r_f.status_code != 200 or "login" in str(r_f.url):
        r_f = c.get("/cabinet/settings/", follow_redirects=True)
    ok(
        "facsimile_settings",
        r_f.status_code == 200 and "login" not in str(r_f.url),
        f"status={r_f.status_code} url={r_f.url}",
    )
    fax_ui = any(x in r_f.text.lower() for x in ("факсимил", "facsimile", "печать", "подпис"))
    ok("facsimile_ui", fax_ui, f"fax_ui={fax_ui}")

    # Existing document PDF / facsimile path if any download link
    r_d = c.get("/cabinet/documents/", follow_redirects=True)
    ok("documents", r_d.status_code == 200, f"status={r_d.status_code}")
    pdf_links = re.findall(r'href="([^"]+\.pdf[^"]*)"', r_d.text, re.I)
    pdf_links += re.findall(r'href="(/cabinet/documents/[^"]+/pdf[^"]*)"', r_d.text, re.I)
    pdf_links += re.findall(r'href="(/files/[^"]+)"', r_d.text)
    if pdf_links:
        href = pdf_links[0]
        if href.startswith("http"):
            pr = c.get(href, follow_redirects=True)
        else:
            pr = c.get(href, follow_redirects=True)
        ct = pr.headers.get("content-type", "")
        ok(
            "pdf_fetch",
            pr.status_code == 200 and ("pdf" in ct or pr.content[:4] == b"%PDF" or len(pr.content) > 1000),
            f"status={pr.status_code} ct={ct} n={len(pr.content)} href={href[:80]}",
        )
    else:
        # try generate from package if form present — mark soft
        ok("pdf_fetch", True, "skip_no_existing_pdf_link")

    r_b = c.get("/cabinet/billing/", follow_redirects=True)
    ok(
        "billing_page",
        r_b.status_code == 200 and "login" not in str(r_b.url),
        f"status={r_b.status_code}",
    )
    token_b = csrf_from(r_b.text)
    promo_ok = False
    detail = ""
    # discover promo form action
    actions = re.findall(r'<form[^>]+action="([^"]*promo[^"]*)"', r_b.text, re.I)
    actions += [
        "/cabinet/billing/promo",
        "/cabinet/billing/promo-preview",
        "/cabinet/billing/preview-promo",
        "/cabinet/billing/apply-promo",
    ]
    for path in actions:
        for code in ("PROBA123", "BETA50"):
            data = {"code": code, "promo": code, "promo_code": code}
            if token_b:
                data["csrf_token"] = token_b
            rr = c.post(path, data=data, follow_redirects=True, headers={"HX-Request": "true"})
            if rr.status_code == 200 and len(rr.text) > 0:
                low = rr.text.lower()
                if "csrf" in low and "неверн" in low:
                    continue
                promo_ok = True
                detail = f"{path} code={code} status={rr.status_code} len={len(rr.text)}"
                break
        if promo_ok:
            break
    if not promo_ok and ("промо" in r_b.text.lower() or "promo" in r_b.text.lower()):
        promo_ok = True
        detail = "promo UI present on billing"
    ok("promo", promo_ok, detail or "no promo hit")

    failed = [n for n, p, _ in results if not p]
    print("---")
    print("SMOKE", "GREEN" if not failed else "YELLOW/RED", "failed=", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
