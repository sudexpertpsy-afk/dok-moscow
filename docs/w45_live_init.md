# W-45 / A-2 — живой Init (прод)

## Что сделано 2026-08-08

1. **G-08 TLS** закрыт: Russian Trusted CA → `GetState`/`Init` снова работают.
2. **Probe Init 10 ₽ + Cancel** на боевом терминале (`PaymentMode.live`):
   - Init `Success=True`, PaymentId=`9006497914`, Status=`NEW`
   - Receipt передан в Init (Taxation/Items)
   - Cancel → `CANCELED`
   - GetState OK (Receipt у отменённого платежа нет — ожидаемо: чек только после CONFIRMED)
3. Код: duplicate webhook / reconcile / `flag_incomplete_receipts` + контракт-тесты.

## Оплата картой

**Подтверждено владельцем (2026-08):** оплата картой на боевом терминале работает — проверено при установке терминала.

Дополнительно в W-45: Init+Cancel probe и починка TLS (Russian Trusted CA), чтобы GetState/reconcile снова ходили в Т-Кассу. Исторические confirmed без `receipt_status` помечены `legacy`; новые incomplete — алерт.
