# Донор QuickDoc+ → Док.Москва

Источник: `/Applications/QuickDoc+.app` v1.14.1 (`info.QuickDocPlus`).
Готовых `.docx` в бандле нет — печать программная. Переносим справочники и состав бланков.

## Модули документов (ProductIDs)

| QuickDoc+ | Целевой шаблон Док.Москва | Волна |
|-----------|---------------------------|-------|
| Bills | Счёт_на_оплату*.docx (уже есть) | — |
| Acts | Акт_оказанных_услуг*.docx (уже есть) | — |
| IncomingCashOrders | ПКО_КО-1.docx (уже есть) | — |
| OutgoingCashOrders | РКО_КО-2.docx | 1 |
| TransferOrders | Платёжное_поручение.docx | 1 |
| UniformDocuments | УПД.docx | 1 |
| Invoices | Счёт_фактура.docx | 1 |
| ReconciliationActReport | Акт_сверки.docx | 1 |
| Waybills | ТОРГ-12 (позже) | 2 |
| WarrantGoods / WarrantInterests | Доверенности (позже) | 2 |
| AdvanceReports | АО-1 (позже) | 2 |
| CashBooks | Кассовая книга (позже) | 2 |

## Справочники

| Файл донора | JSON в `web/app/data/refs/` |
|-------------|------------------------------|
| Units.plist | units.json |
| BudgetCodes.plist | kbk.json |
| Payments.plist | payment_bases.json (+ payment_*) |
| TaxRates.plist | tax_rates.json (18%→20%) |
| Countries.plist | countries.json |
| Currencies.plist | currencies.json |
| Territories.plist | territories.json (серверный поиск) |
| Preferences.plist | document_defaults.json |

Сырые конверты: `docs/donors/quickdoc-plus/raw/`, нормализованные копии: `json/`.

## Не переносим

UI/nib, бинарник, IAP, CDN `resources.quickdoc.info` (банки).
