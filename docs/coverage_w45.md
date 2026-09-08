# W-45 / Б-3 — coverage денежных и внешних модулей

Прогон (релевантные тесты + `test_w45_coverage_boost.py`), 2026-08-08:

| Модуль | Cover | Цель ≥60% |
|--------|------:|:---------:|
| `app.billing.payments` | 75% | ✅ |
| `app.billing.jobs` | 63% | ✅ |
| `app.billing.tbank` | 94% | ✅ |
| `app.services.dadata` | 78% | ✅ |
| `app.ops_agent.actions` | 66% | ✅ |
| `app.ops_agent.app` | 69% | ✅ |
| **TOTAL (эти модули)** | **74%** | ✅ |

Команда:

```bash
cd web && pytest \
  tests/test_w45_coverage_boost.py tests/test_billing_w1*.py \
  tests/test_billing_tbank_ready.py tests/test_counterparties.py \
  tests/test_party_check_w21.py tests/test_bank_settings_invoice.py \
  tests/test_ops_w32.py tests/test_ops_agent_w34.py tests/test_w45_contracts.py \
  --cov=app.billing.payments --cov=app.billing.tbank --cov=app.billing.jobs \
  --cov=app.services.dadata --cov=app.ops_agent --cov-report=term
```
