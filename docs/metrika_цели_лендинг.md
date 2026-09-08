# Яндекс.Метрика: цели лендинга (счётчик 92437634)

Задача для владельца счётчика в интерфейсе Метрики
(Настройка → Цели → JavaScript-событие).

| Идентификатор | Когда срабатывает |
|---------------|-------------------|
| `sample_pdf` | Открытие образца в модалке (параметр `doc`) |
| `engaged` | Скролл >50% / якорь меню / FAQ / таб тарифов |
| `demo_inn_submit` | Отправка ИНН в демо |
| `demo_inn_success` | Успешная карточка (api или fallback) |
| `demo_to_apply_click` | CTA из демо к заявке |
| `faq_open` | Раскрытие вопроса FAQ |
| `apply_form_start` | Фокус первого поля заявки |
| `apply_form_submit` | Отправка формы (и flash OK) |
| `login_click` | Клик «Войти» |
| `calc_interact` | Первое движение слайдера калькулятора |
| `calc_to_apply_click` | CTA из калькулятора |
| `content_card_click` | Карточка «Полезное» |
| `sticky_apply_click` | Sticky «Заявка» на мобильных |

Код вызывает `ym(ID,'reachGoal', goal, params)` через `window.dokTrack`.
Не включать искусственный короткий `accurateTrackBounce` ради снижения отказов.
