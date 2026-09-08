# W-45 / Б-3 — Playwright кабинет

Прогон: `W45_PLAYWRIGHT=1 pytest tests/test_playwright_cabinet_w45.py` — **OK** (2026-08-08).

- 8 страниц кабинета под `org_admin`
- viewport 375×812
- ноль `pageerror` (JS)
- uvicorn поднимается на том же test-app, что и фикстура
