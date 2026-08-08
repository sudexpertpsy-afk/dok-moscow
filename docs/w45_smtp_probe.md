# W-45 / Б-2 — SMTP probe

Дата: 2026-08-08. Получатель: `sudexpertpsy@gmail.com` (ADMIN_NOTIFY).

| Тип | Результат |
|-----|-----------|
| invite | OK |
| password_reset | OK |
| payment_receipt | OK |
| subscription_ending | OK |
| npa_change | OK |
| alert | OK |
| staff_deactivate | OK |
| admin_extend | OK |
| admin_sub_change | OK |

`app_base_url=https://app.dok.moscow` (https). Повторить:  
`docker compose exec app python scripts/w45_smtp_probe.py --to …`
