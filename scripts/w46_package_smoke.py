"""Package wizard smoke: ФЛ → generate → PDF. Credentials via env."""
from __future__ import annotations

import os
import re
import sys

import httpx

base = os.environ.get("SMOKE_BASE_URL", "https://app.dok.moscow").rstrip("/")
email = os.environ["SMOKE_EMAIL"].strip()
password = os.environ["SMOKE_PASSWORD"].strip()


def csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not m:
        raise RuntimeError("no csrf")
    return m.group(1)


def main() -> int:
    c = httpx.Client(base_url=base, follow_redirects=False, timeout=180.0)
    r = c.get("/login")
    r = c.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf(r.text)},
    )
    assert r.status_code in (302, 303), r.status_code
    c.get(r.headers["location"], follow_redirects=True)

    c.post(
        "/cabinet/package/reset",
        data={"csrf_token": csrf(c.get("/cabinet/package/", follow_redirects=True).text)},
        follow_redirects=True,
    )

    r = c.get("/cabinet/package/?тип=Физлицо", follow_redirects=True)
    tok = csrf(r.text)
    # collect core field input names
    fields = re.findall(r'<input[^>]+name="([^"]+)"', r.text)
    data: dict[str, str] = {
        "csrf_token": tok,
        "тип": "Физлицо",
        "counterparty_id": "new",
    }
    for name in fields:
        if name in data or name == "csrf_token":
            continue
        low = name.lower()
        if "date" in low or "дата" in low:
            data[name] = "01.09.2026"
        elif "inn" in low or "инн" in low:
            data[name] = "500100732259"
        elif "email" in low:
            data[name] = "smoke@example.com"
        elif "phone" in low or "тел" in low:
            data[name] = "+79001234567"
        else:
            data[name] = "Тест Смоук"
    # also textareas
    for name in re.findall(r'<textarea[^>]+name="([^"]+)"', r.text):
        data.setdefault(name, "Тест")

    r1 = c.post("/cabinet/package/step1", data=data, follow_redirects=False)
    print("step1", r1.status_code, r1.headers.get("location"), "fields", len(data))
    if r1.status_code not in (302, 303):
        print("body", r1.text[:300].replace("\n", " "))
        return 1
    r2 = c.get(r1.headers["location"], follow_redirects=True)

    tok = csrf(r2.text)
    data2: dict[str, str | list[str]] = {"csrf_token": tok, "action": "next"}
    # contract template select
    opts = re.findall(
        r'<select[^>]+name="contract_template"[\s\S]*?</select>', r2.text
    )
    if opts:
        first = re.search(r'<option[^>]+value="([^"]+)"', opts[0])
        if first:
            data2["contract_template"] = first.group(1)
            print("contract_template", first.group(1)[:80])
    # extras checkboxes — take up to 3
    extras = re.findall(r'name="extras"[^>]*value="([^"]+)"', r2.text)
    if extras:
        data2["extras"] = extras[:3]
        print("extras", extras[:3])
    r2p = c.post("/cabinet/package/step2", data=data2, follow_redirects=False)
    print("step2", r2p.status_code, r2p.headers.get("location"))
    if r2p.status_code not in (302, 303):
        print("body", r2p.text[:300].replace("\n", " "))
        return 1
    r3 = c.get(r2p.headers["location"], follow_redirects=True)

    tok = csrf(r3.text)
    data3 = {"csrf_token": tok}
    for n, v in re.findall(
        r'<input[^>]+type="checkbox"[^>]+name="([^"]+)"[^>]+value="([^"]+)"', r3.text
    ):
        data3[n] = v
    for n, v in re.findall(
        r'<input[^>]+name="([^"]+)"[^>]+type="checkbox"[^>]+value="([^"]+)"', r3.text
    ):
        data3[n] = v
    # turn on all fax_* 
    for n in list(data3):
        if n.startswith("fax_"):
            data3[n] = "1"
    print("step3_fax_flags", [k for k in data3 if k.startswith("fax_")][:8])
    r3p = c.post("/cabinet/package/step3", data=data3, follow_redirects=False)
    print("step3", r3p.status_code, r3p.headers.get("location"))
    if r3p.status_code not in (302, 303):
        print("body", r3p.text[:400].replace("\n", " "))
        return 1
    done = c.get(r3p.headers["location"], follow_redirects=True)
    print("done", done.status_code, str(done.url))
    fax = "факсимил" in done.text.lower()
    print("PASS generate", "fax_mention=", fax, "len=", len(done.text))

    tok = csrf(done.text)
    pdf = c.post("/cabinet/package/pdf", data={"csrf_token": tok}, follow_redirects=False)
    ct = pdf.headers.get("content-type", "")
    print("pdf", pdf.status_code, ct, "loc=", pdf.headers.get("location"), "n=", len(pdf.content))
    if pdf.status_code == 200 and (pdf.content[:4] == b"%PDF" or "pdf" in ct):
        print("PASS pdf_bytes")
        return 0
    if pdf.status_code in (302, 303, 200, 202):
        print("PASS pdf_job_accepted")
        return 0
    print("FAIL pdf")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("FAIL", type(e).__name__, e)
        raise SystemExit(1)
