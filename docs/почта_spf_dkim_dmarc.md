# Доставляемость почты: SPF, DKIM, DMARC

Цель: письма алертов и сервиса попадали во «Входящие» (Gmail/Mail.ru),
а не в спам и не блокировались по частоте/аутентификации.

## Текущее состояние (прод, 2026-08-08)

| Параметр | Значение |
|----------|----------|
| SMTP | `smtp.gmail.com:587` (аккаунт Gmail) |
| `SMTP_FROM` | `Dok.Moscow <sudexpertpsy@gmail.com>` |
| `ADMIN_NOTIFY_EMAIL` | `sudexpertpsy@gmail.com` |

Пока релей — Gmail, **From должен совпадать с доменом Gmail**
(или Google Workspace). Подставлять `alerts@dok.moscow` через
`smtp.gmail.com` без Workspace для `dok.moscow` ломает SPF/DMARC.

HOTFIX вебхук-алертов (дедуп + operational без писем) уже снижает
объём и снимает блокировку по частоте.

## Целевая схема (рекомендация W-50: Яндекс 360)

Личный `gmail.com` как From для `@dok.moscow` **не подходит**: DKIM домена
через Gmail не получить, SPF/DMARC для dok.moscow не сойдутся. Нужен ящик
на домене.

Краткий путь — **Яндекс 360 для бизнеса** (домен dok.moscow):

1. Подключить домен, создать `noreply@dok.moscow` (или `alerts@…`).
2. DNS: SPF `v=spf1 include:_spf.yandex.net ~all`; DKIM из панели Яндекса;
   DMARC `v=DMARC1; p=none; rua=mailto:…` (позже `p=quarantine`).
3. В `deploy/.env`:
   ```bash
   SMTP_HOST=smtp.yandex.ru
   SMTP_PORT=465
   SMTP_USE_TLS=false
   SMTP_USER=noreply@dok.moscow
   SMTP_PASSWORD=...   # пароль приложения
   SMTP_FROM="Док.Москва <noreply@dok.moscow>"
   ```
   (порт 465 — implicit SSL в `send_email`; альтернатива 587 + `SMTP_USE_TLS=true`)
4. После выкладки — `/admin/status` → «Тест доставляемости» → PASS в оригинале
   → «Подтвердить» (маркер с `from_domain=dok.moscow`).

Альтернатива — почта хостера / Workspace; смысл тот же: From и DKIM на
`dok.moscow`.

---

## Целевая схема (общее)

1. Почтовый ящик/релей на своём домене, например:
   - Яндекс 360 / Mail.ru для бизнеса / Google Workspace для `dok.moscow`;
   - либо транзакционный SMTP (с поддержкой кастомного From).
2. В `deploy/.env`:
   ```bash
   SMTP_HOST=...
   SMTP_PORT=587
   SMTP_USER=alerts@dok.moscow
   SMTP_PASSWORD=...
   SMTP_FROM="Док.Москва <alerts@dok.moscow>"
   SMTP_USE_TLS=true
   ADMIN_NOTIFY_EMAIL=...   # куда слать алерты (можно личный Gmail)
   ```

## DNS-записи (зона `dok.moscow`)

Подставьте значения из панели почтового провайдера.

### SPF (TXT на `@` или на поддомене отправки)

```
v=spf1 include:_spf.yandex.net ~all
```

(пример для Яндекса; для Google — `include:_spf.google.com`; для
Mail.ru — `include:_spf.mail.ru`. Если несколько релеев — один SPF с
несколькими `include:`.)

### DKIM

TXT-запись вида `selector._domainkey.dok.moscow` — значение выдаёт
провайдер при включении DKIM. Без DKIM Gmail часто кладёт в спам.

### DMARC (TXT на `_dmarc.dok.moscow`)

Старт (мониторинг):

```
v=DMARC1; p=none; rua=mailto:dmarc@dok.moscow; fo=1
```

После стабильных pass — ужесточить:

```
v=DMARC1; p=quarantine; pct=100; rua=mailto:dmarc@dok.moscow
```

## Проверка после смены DNS/SMTP

1. Подождать TTL DNS (часто 5–60 мин).
2. Тест:
   ```bash
   docker compose -f deploy/compose.yml exec app \
     python scripts/w45_smtp_probe.py --to YOU@gmail.com
   ```
   и отдельно на ящик Mail.ru.
3. В письме открыть «Показать оригинал» / заголовки:
   `Authentication-Results: … spf=pass … dkim=pass … dmarc=pass`.
4. Письмо во «Входящих», не в «Спам».

## Связь с аудитом №2

Закрывает пункт «SMTP вживую» / доставляемость: после настройки
релея и DNS повторить probe и зафиксировать pass в этом файле
(дата + получатели).
