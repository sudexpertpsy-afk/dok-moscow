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

## Целевая схема

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
