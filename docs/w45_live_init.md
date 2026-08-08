# W-45 / A-2 — живой Init (прод)

## Что сделано 2026-08-08

1. **G-08 TLS** закрыт: Russian Trusted CA → `GetState`/`Init` снова работают.
2. **Probe Init 10 ₽ + Cancel** на боевом терминале (`PaymentMode.live`):
   - Init `Success=True`, PaymentId=`9006497914`, Status=`NEW`
   - Receipt передан в Init (Taxation/Items)
   - Cancel → `CANCELED`
   - GetState OK (Receipt у отменённого платежа нет — ожидаемо: чек только после CONFIRMED)
3. Код: duplicate webhook / reconcile / `flag_incomplete_receipts` + контракт-тесты.

## Что остаётся владельцу (карта)

Полный цикл 54-ФЗ (чек в кабинете + возврат) требует **оплаты картой** минимальной суммы в `/cabinet/billing/pay`, затем возврат в ЛК Т-Банка. Автоматически без карты закрыть нельзя.

После оплаты проверить:
- `payments.receipt_status` / `receipt_url` не пустые
- ссылка «Чек» в кабинете и `/admin/payments/{id}`
- после возврата — status `refunded` + чек возврата
