# W-45 PageSpeed (mobile)

`PAGESPEED_API_KEY` в окружении агента и на проде **не задан** — замер не выполнен.

Когда ключ появится:

```bash
PAGESPEED_API_KEY=… python scripts/w45_pagespeed.py
```

Цель: performance ≥ 90 на `/`, `/tariffs`, `/zakon/`, `/obraztsy`.
