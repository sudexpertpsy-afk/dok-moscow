"""Единый реестр разделов и функций кабинетов (W-23).

Меню кабинета и админки, глобальный поиск и палитра Cmd+K
строятся только из этого списка — один источник правды о доступе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.models import TariffCode


class NavRole(str, Enum):
    """Роли доступа к пунктам навигации.

    org_user — любой сотрудник организации (member + admin);
    org_admin — только администратор организации (users.org_role).
    """

    org_user = "org_user"
    org_admin = "org_admin"
    service_admin = "service_admin"


ORG_ROLES = frozenset({NavRole.org_user, NavRole.org_admin})
ALL_ROLES = frozenset({NavRole.org_user, NavRole.org_admin, NavRole.service_admin})
PAID = frozenset({TariffCode.specialist, TariffCode.organization})
ORG_TARIFF = frozenset({TariffCode.organization})


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    url: str
    synonyms: tuple[str, ...] = ()
    roles: frozenset[NavRole] = field(default_factory=lambda: ORG_ROLES)
    tariffs: frozenset[TariffCode] | None = None  # None = любой тариф
    area: str = "cabinet"  # cabinet | admin | public | action
    group: str = "Разделы"
    menu: bool = True
    quick_action: bool = False
    focus_selector: str = ""


def _item(
    key: str,
    title: str,
    url: str,
    *,
    synonyms: tuple[str, ...] = (),
    roles: frozenset[NavRole] = ORG_ROLES,
    tariffs: frozenset[TariffCode] | None = None,
    area: str = "cabinet",
    group: str = "Разделы",
    menu: bool = True,
    quick_action: bool = False,
    focus_selector: str = "",
) -> NavItem:
    return NavItem(
        key=key,
        title=title,
        url=url,
        synonyms=synonyms,
        roles=roles,
        tariffs=tariffs,
        area=area,
        group=group,
        menu=menu,
        quick_action=quick_action,
        focus_selector=focus_selector,
    )


NAV_REGISTRY: tuple[NavItem, ...] = (
    _item(
        "home",
        "Главная",
        "/cabinet/",
        synonyms=("дашборд", "обзор", "требуют внимания", "старт"),
        group="Работа",
    ),
    _item(
        "documents",
        "Документы",
        "/cabinet/documents/",
        synonyms=("документ", "отдельный документ", "заполнить", "шаблонер"),
        group="Работа",
    ),
    _item(
        "package",
        "Комплект",
        "/cabinet/package/",
        synonyms=("новый комплект", "пакет документов", "мастер комплекта", "договор"),
        group="Работа",
    ),
    _item(
        "templates",
        "Мои шаблоны",
        "/cabinet/templates/",
        synonyms=("свои шаблоны", "docx организации", "загрузить шаблон"),
        roles=frozenset({NavRole.org_admin}),
        tariffs=ORG_TARIFF,
        group="Работа",
    ),
    _item(
        "template_fields",
        "Поля шаблонов",
        "/cabinet/templates/fields",
        synonyms=("плейсхолдеры", "свои поля", "словарь полей", "org fields"),
        roles=ORG_ROLES,
        tariffs=ORG_TARIFF,
        group="Работа",
    ),
    _item(
        "counterparties",
        "Контрагенты",
        "/cabinet/counterparties/",
        synonyms=("картотека", "клиенты", "юрлицо", "физлицо", "эксперт"),
        group="Работа",
    ),
    _item(
        "party_check",
        "Проверка контрагента",
        "/cabinet/party-check/",
        synonyms=("проверка", "егрюл", "инн", "огрн", "dadata", "должная осмотрительность"),
        tariffs=PAID,
        group="Работа",
    ),
    _item(
        "journal",
        "Журнал",
        "/cabinet/journal",
        synonyms=("история документов", "реестр документов"),
        group="Работа",
    ),
    _item(
        "calendar",
        "Календарь",
        "/cabinet/calendar/",
        synonyms=("события", "планы", "напоминания", "сроки"),
        group="Работа",
    ),
    _item(
        "zakon_nav",
        "Законодательство",
        "/cabinet/zakon/",
        synonyms=("нпа", "законы", "нормативка", "кодекс", "закладки", "заметки"),
        group="Справка",
    ),
    _item(
        "help",
        "Как пользоваться",
        "/cabinet/help/",
        synonyms=(
            "помощь",
            "инструкции",
            "справка",
            "faq",
            "руководство",
            "обучение",
            "шаблоны",
        ),
        group="Справка",
    ),
    _item(
        "support",
        "Поддержка",
        "/cabinet/support/",
        synonyms=(
            "тикет",
            "обращение",
            "баг",
            "ошибка",
            "улучшение",
            "предложить",
            "feedback",
        ),
        group="Справка",
    ),
    _item(
        "billing",
        "Тариф и оплата",
        "/cabinet/billing/",
        synonyms=("оплата", "подписка", "тариф", "счёт", "карта"),
        group="Организация",
    ),
    _item(
        "staff",
        "Сотрудники",
        "/cabinet/staff/",
        synonyms=("коллеги", "пригласить", "пользователи организации", "инвайт"),
        roles=frozenset({NavRole.org_admin}),
        tariffs=ORG_TARIFF,
        group="Организация",
    ),
    _item(
        "settings",
        "Настройки",
        "/cabinet/settings/",
        synonyms=("реквизиты", "организация", "профиль"),
        # Видно всем: у member — только реквизиты (RO) и безопасность
        roles=ORG_ROLES,
        group="Организация",
    ),
    _item(
        "settings_bank",
        "Банковские реквизиты",
        "/cabinet/settings/bank",
        synonyms=("банк", "р/с", "бик", "корр"),
        roles=frozenset({NavRole.org_admin}),
        menu=False,
        group="Организация",
    ),
    _item(
        "settings_signatories",
        "Подписанты",
        "/cabinet/settings/signatories",
        synonyms=("подпись", "директор", "доверенность"),
        roles=frozenset({NavRole.org_admin}),
        menu=False,
        group="Организация",
    ),
    _item(
        "settings_branding",
        "Печать и подписи",
        "/cabinet/settings/branding",
        synonyms=("факсимиле", "печать", "штамп", "подпись в pdf"),
        roles=frozenset({NavRole.org_admin}),
        menu=False,
        tariffs=PAID,
        group="Организация",
    ),
    _item(
        "settings_price",
        "Прайс",
        "/cabinet/settings/price",
        synonyms=("цены", "стоимость услуг"),
        roles=frozenset({NavRole.org_admin}),
        menu=False,
        group="Организация",
    ),
    _item(
        "settings_numbering",
        "Нумерация документов",
        "/cabinet/settings/numbering",
        synonyms=("нумерация", "счётчики", "префикс", "номер договора"),
        roles=frozenset({NavRole.org_admin}),
        menu=False,
        group="Организация",
    ),
    _item(
        "settings_security",
        "Безопасность",
        "/cabinet/settings/security",
        synonyms=("пароль", "смена пароля"),
        menu=False,
        group="Организация",
    ),
    _item(
        "cabinet_search",
        "Поиск по кабинету",
        "/cabinet/search",
        synonyms=("найти", "сквозной поиск"),
        menu=False,
        group="Работа",
    ),
    _item(
        "party_check_journal",
        "Журнал проверок контрагентов",
        "/cabinet/party-check/journal",
        synonyms=("должная осмотрительность", "история проверок"),
        tariffs=PAID,
        menu=False,
        group="Работа",
    ),
    _item(
        "qa_package",
        "Новый комплект",
        "/cabinet/package/?autofocus=1",
        synonyms=("создать комплект", "пакет"),
        menu=False,
        quick_action=True,
        focus_selector="#тип, select[name='тип'], #main input, #main select",
        group="Быстрые действия",
    ),
    _item(
        "qa_document",
        "Новый документ",
        "/cabinet/documents/?autofocus=1",
        synonyms=("создать документ", "заполнить шаблон"),
        menu=False,
        quick_action=True,
        focus_selector="#main a.btn, #main input",
        group="Быстрые действия",
    ),
    _item(
        "qa_counterparty",
        "Добавить контрагента",
        "/cabinet/counterparties/new?type=ul&autofocus=1",
        synonyms=("новый контрагент", "создать юрлицо"),
        menu=False,
        quick_action=True,
        focus_selector="#name, #fio, #main input",
        group="Быстрые действия",
    ),
    _item(
        "qa_party_check",
        "Проверить контрагента по ИНН",
        "/cabinet/party-check/?autofocus=1",
        synonyms=("проверка инн", "егрюл", "огрн"),
        tariffs=PAID,
        menu=False,
        quick_action=True,
        focus_selector="#query, #main input",
        group="Быстрые действия",
    ),
    _item(
        "zakon",
        "Законодательство",
        "/zakon/",
        synonyms=("нпа", "закон", "кодекс", "экспертиза", "право", "нормы"),
        roles=ALL_ROLES,
        area="public",
        menu=False,
        group="Справка",
    ),
    _item(
        "admin_home",
        "Обзор",
        "/admin/",
        synonyms=("дашборд", "сводка"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_orgs",
        "Организации",
        "/admin/organizations",
        synonyms=("подписчики", "клиенты сервиса", "орг"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_users",
        "Пользователи",
        "/admin/users",
        synonyms=("аккаунты", "учётки"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_leads",
        "Заявки",
        "/admin/leads",
        synonyms=("лиды", "лендинг", "заявки с сайта"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_support",
        "Обращения",
        "/admin/support/",
        synonyms=("поддержка", "тикеты", "улучшения", "feedback", "баги"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_invites",
        "Приглашения",
        "/admin/invites",
        synonyms=("инвайт", "пригласить"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_templates",
        "Шаблоны",
        "/admin/templates",
        synonyms=("общие шаблоны", "docx", "пко", "счёт", "акт"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_cms",
        "Единое окно",
        "/admin/cms/",
        synonyms=("лендинг", "cms", "контент", "промокоды", "тарифы", "объявления"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_security",
        "Безопасность",
        "/admin/security/",
        synonyms=(
            "пароль",
            "2fa",
            "totp",
            "смена пароля",
            "яндекс id",
            "профиль админа",
            "безопасность владельца",
        ),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_legal",
        "Законодательство",
        "/admin/legal/",
        synonyms=("нпа", "законы", "редакции", "мониторинг нпа", "zakon"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_payments",
        "Платежи",
        "/admin/payments",
        synonyms=("оплаты", "транзакции", "возврат"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_paysettings",
        "Платёжная система",
        "/admin/payment-settings",
        synonyms=("касса", "т-касса", "tbank", "терминал", "эквайринг"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_status",
        "Статус",
        "/admin/status",
        synonyms=("здоровье", "конфигурация", "dadata", "gotenberg"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
    _item(
        "admin_server",
        "Сервер",
        "/admin/server/",
        synonyms=("ops", "ops-agent", "бэкап", "деплой", "hostland", "vds"),
        roles=frozenset({NavRole.service_admin}),
        area="admin",
        group="Администрирование",
    ),
)


# Технические маршруты вне NAV_REGISTRY (W-33: каждый — с обоснованием).
ROUTE_EXCEPTION_NOTES: dict[str, str] = {
    "/": "лендинг; пункт не в сайдбаре кабинета",
    "/login": "форма входа",
    "/login/2fa": "шаг 2FA",
    "/logout": "POST выход",
    "/signup": "self-serve регистрация (W-47)",
    "/verify-email/{token}": "подтверждение e-mail (W-47)",
    "/confirm-signup/{token}": "подтверждение self-serve Signup (W-50)",
    "/cabinet/verify-email/resend": "POST повтор письма подтверждения",
    "/forgot-password": "сброс пароля",
    "/reset-password/{token}": "ссылка из письма",
    "/invite/{token}": "принятие инвайта",
    "/auth/yandex/start": "OAuth старт",
    "/auth/yandex/callback": "OAuth callback",
    "/apply": "заявка с лендинга",
    "/healthz": "healthcheck",
    "/privacy": "юр. страница",
    "/offer": "юр. страница",
    "/requisites": "юр. страница",
    "/tariffs": "публичные тарифы",
    "/contacts": "контакты",
    "/obraztsy": "библиотека образцов",
    "/obraztsy/{slug}": "карточка образца шаблона",
    "/dlya-ekspertov": "сегментный лендинг",
    "/dlya-organizatsiy": "сегментный лендинг",
    "/dlya-uchebnykh-tsentrov": "сегментный лендинг",
    "/bezopasnost": "страница доверия",
    "/novoe": "лента релизов",
    "/praktika": "раздел Практика",
    "/praktika/{slug}": "статья Практики",
    "/robots.txt": "SEO",
    "/sitemap.xml": "SEO",
    "/billing/webhook": "вебхук Т-Кассы",
    "/api/global-search": "JSON Cmd+K",
    "/api/nav-order": "JSON порядок меню",
    "/openapi.json": "отключено в проде (docs_url=None)",
    "/docs": "отключено в проде",
    "/redoc": "отключено в проде",
    "/cabinet/": "дашборд; в реестре как home с /cabinet/",
    "/cabinet/package": "редирект на /cabinet/package/",
    "/cabinet/counterparties": "редирект на список",
    "/cabinet/settings": "редирект на настройки",
    "/cabinet/counterparties/suggest/party": "HTMX DaData",
    "/cabinet/counterparties/suggest/address": "HTMX DaData",
    "/cabinet/counterparties/suggest/bank": "HTMX DaData",
    "/cabinet/form-assist/suggest": "HTMX history полей формы",
    "/cabinet/form-assist/linked": "JSON связанные поля",
    "/cabinet/form-assist/peek-numbers": "JSON peek счётчиков",
    "/cabinet/form-assist/refs": "HTMX справочники ОКЕИ/КБК/НДС",
    "/cabinet/counterparties/{cp_id}": "карточка сущности",
    "/cabinet/counterparties/{cp_id}/edit": "форма сущности",
    "/cabinet/counterparties/{cp_id}/delete": "действие",
    "/cabinet/counterparties/new": "создание",
    "/cabinet/counterparties/import": "W-49 A импорт",
    "/cabinet/counterparties/import/template.xlsx": "шаблон Excel",
    "/cabinet/counterparties/import/upload": "POST загрузка",
    "/cabinet/counterparties/import/map": "сопоставление колонок",
    "/cabinet/counterparties/import/preview": "предпросмотр",
    "/cabinet/counterparties/import/run": "POST импорт",
    "/cabinet/counterparties/import/done": "итог",
    "/cabinet/counterparties/import/report": "xlsx ошибок",
    "/cabinet/documents/new/{template_name}": "форма генерации",
    "/cabinet/documents/{doc_id}": "карточка документа",
    "/cabinet/documents/{doc_id}/download": "файл",
    "/cabinet/documents/{doc_id}/pdf": "PDF/job",
    "/cabinet/package/step1": "мастер комплекта",
    "/cabinet/package/step2": "мастер комплекта",
    "/cabinet/package/step3": "мастер комплекта",
    "/cabinet/package/done": "мастер комплекта",
    "/cabinet/package/zip": "выгрузка",
    "/cabinet/package/pdf": "выгрузка",
    "/cabinet/package/reset": "сброс мастера",
    "/cabinet/package/repeat/{doc_id}": "повтор комплекта",
    "/cabinet/templates/upload": "POST шаблона",
    "/cabinet/templates/upload/confirm": "POST подтверждения шаблона",
    "/cabinet/templates/upload/cancel": "POST отмены загрузки",
    "/cabinet/templates/rename": "POST шаблона",
    "/cabinet/templates/delete": "POST шаблона",
    "/cabinet/templates/role": "POST шаблона",
    "/cabinet/templates/fields/create": "POST поля",
    "/cabinet/templates/fields": "GET поля",

    "/cabinet/calendar/new": "форма события",
    "/cabinet/calendar/{event_id}": "карточка события",
    "/cabinet/calendar/{event_id}/done": "действие",
    "/cabinet/calendar/{event_id}/delete": "действие",
    "/cabinet/support/improve": "форма предложения улучшения",
    "/cabinet/support/{id}": "просмотр обращения",
    "/cabinet/support/{id}/attachment": "скачивание вложения тикета",
    "/cabinet/billing/pay": "старт оплаты",
    "/cabinet/billing/promo-preview": "HTMX предпросмотр промокода",
    "/cabinet/billing/success": "возврат Т-Кассы",
    "/cabinet/billing/fail": "возврат Т-Кассы",
    "/cabinet/settings/bank": "вкладка настроек",
    "/cabinet/settings/signatories": "вкладка настроек",
    "/cabinet/settings/branding": "W-43 печать и подписи",
    "/cabinet/settings/branding/prefs": "POST prefs факсимиле",
    "/cabinet/settings/branding/check": "POST проверка изображения",
    "/cabinet/settings/branding/{slot}/upload": "POST загрузка слота",
    "/cabinet/settings/branding/{slot}/delete": "POST удаление слота",
    "/cabinet/settings/branding/{slot}/preview.png": "GET превью",
    "/cabinet/settings/price": "вкладка настроек",
    "/cabinet/settings/numbering": "W-49 нумерация",
    "/cabinet/settings/counters": "редирект на numbering",
    "/cabinet/settings/data": "W-49 C выгрузка данных",
    "/cabinet/settings/data/export": "POST выгрузка ZIP",
    "/cabinet/onboarding/dismiss": "POST скрыть чеклист",
    "/cabinet/settings/security": "вкладка безопасности",
    "/cabinet/settings/security/2fa/start": "2FA",
    "/cabinet/settings/security/2fa/confirm": "2FA",
    "/cabinet/settings/security/2fa/disable": "2FA",
    "/cabinet/settings/security/2fa/dismiss-backup": "2FA",
    "/cabinet/settings/security/2fa/backup.txt": "2FA файл",
    "/cabinet/settings/security/password": "смена пароля",
    "/cabinet/settings/security/yandex/link": "OAuth привязка",
    "/cabinet/settings/security/yandex/unlink": "OAuth отвязка",
    "/cabinet/staff": "редирект на /cabinet/staff/",
    "/cabinet/staff/": "список; пункт staff в реестре",
    "/cabinet/staff/invite": "POST инвайт",
    "/cabinet/staff/{user_id}/deactivate": "POST",
    "/cabinet/staff/{user_id}/transfer-admin": "POST",
    "/cabinet/jobs/{job_id}": "прогресс фоновой задачи",
    "/cabinet/jobs/{job_id}/download": "результат job",
    "/cabinet/journal/rows": "HTMX подгрузка журнала",
    "/cabinet/party-check/search": "шаг проверки",
    "/cabinet/party-check/card": "шаг проверки",
    "/cabinet/party-check/select": "шаг проверки",
    "/cabinet/party-check/save": "шаг проверки",
    "/cabinet/party-check/pdf": "выгрузка",
    "/cabinet/party-check/package": "выгрузка",
    "/cabinet/party-check/journal/{check_id}": "карточка проверки",
    "/zakon/{slug}": "карточка НПА; индекс в реестре",
    "/cabinet/zakon/": "каталог НПА в кабинете (W-35)",
    "/cabinet/zakon/search": "поиск НПА → редирект на каталог",
    "/cabinet/zakon/{slug}": "акт/статьи в кабинете",
    "/cabinet/zakon/{slug}/bookmark": "POST закладка",
    "/cabinet/zakon/{slug}/watch": "POST слежение (платный)",
    "/cabinet/zakon/{slug}/note/{fragment_id}": "POST заметка (платный)",
    "/cabinet/zakon/{slug}/quote": "JSON цитата с реквизитом",
    "/cabinet/zakon/{slug}/export.docx": "DOCX извлечение (платный)",
    "/admin/organizations": "список; пункт в реестре без trailing",
    "/admin/organizations/purge-mode": "POST W-50.1 dry|live",
    "/admin/organizations/{org_id}": "карточка орг",
    "/admin/organizations/{org_id}/rename": "POST",
    "/admin/organizations/{org_id}/internal": "POST W-50 is_internal",
    "/admin/organizations/{org_id}/purge": "POST W-50 purge пустой орг",
    "/admin/organizations/{org_id}/subscription": "POST W-38 управление подпиской",
    "/admin/users/{user_id}": "карточка пользователя",
    "/admin/users/{user_id}/toggle": "POST",
    "/admin/users/{user_id}/reset-2fa": "POST",
    "/admin/leads/{lead_id}/invite": "POST инвайт в существующую орг",
    "/admin/leads/{lead_id}/create-org": "POST W-39 орг+подписка+инвайт",
    "/admin/leads/{lead_id}/resend": "POST повторный инвайт",
    "/admin/leads/{lead_id}/reject": "POST отклонить заявку",
    "/admin/leads/{lead_id}/spam": "POST пометить спам",
    "/admin/leads/{lead_id}/note": "POST заметка администратора",
    "/admin/leads/{lead_id}/edit": "POST правка полей заявки",
    "/admin/leads/{lead_id}/reopen": "POST вернуть заявку в новые",
    "/admin/leads/{lead_id}/delete": "POST удаление заявки",
    "/admin/support/{id}": "карточка обращения",
    "/admin/support/{id}/attachment": "вложение обращения (админ)",
    "/admin/invites": "список инвайтов",
    "/admin/invites/{invite_id}/revoke": "POST",
    "/admin/invites/{invite_id}/resend": "POST повтор приглашения",
    "/admin/invites/{invite_id}/delete": "POST удаление приглашения",
    "/admin/payments/export": "xlsx",
    "/admin/payments/{payment_id}": "карточка платежа",
    "/admin/payments/{payment_id}/reconcile": "POST",
    "/admin/payments/{payment_id}/mark-refund": "POST",
    "/admin/subscriptions/manual-extend": "POST",
    "/admin/payment-settings/test": "POST проверка кассы",
    "/admin/templates/upload": "POST",
    "/admin/templates/rename": "POST",
    "/admin/templates/delete": "POST",
    "/admin/templates/role": "POST",
    "/admin/security/password": "смена пароля service_admin",
    "/admin/security/2fa/start": "2FA владельца",
    "/admin/security/2fa/confirm": "2FA владельца",
    "/admin/security/2fa/disable": "2FA владельца",
    "/admin/security/2fa/dismiss-backup": "2FA владельца",
    "/admin/security/2fa/backup.txt": "2FA файл владельца",
    "/admin/security/yandex/link": "OAuth привязка владельца",
    "/admin/security/yandex/unlink": "OAuth отвязка владельца",
    "/admin/cms/tariffs": "раздел единого окна; POST тарифов с TOTP",
    "/admin/cms/content": "редактор CMS-слотов",
    "/admin/cms/content/preview": "HTMX preview Markdown",
    "/admin/cms/content/publish": "POST публикация CMS-слота",
    "/admin/cms/content/{key}/rollback/{version}": "POST откат CMS-слота",
    "/admin/cms/promos": "CRUD промокодов",
    "/admin/cms/promos/{promo_id}/delete": "POST выключение промокода",
    "/admin/cms/announcements": "CRUD публичных объявлений",
    "/admin/legal/new": "форма нового акта НПА",
    "/admin/legal/bootstrap": "POST пакетное наполнение из ИПС",
    "/admin/legal/publish-drafts": "POST пакетная публикация черновиков",
    "/admin/legal/{act_id}": "карточка акта",
    "/admin/legal/{act_id}/publish/{version_id}": "POST публикация редакции",
    "/admin/legal/{act_id}/reject/{version_id}": "POST отклонение черновика",
    "/admin/legal/{act_id}/pull-ips": "POST подтянуть текст из ИПС",
    "/admin/legal/{act_id}/upload": "POST ручная загрузка",
    "/admin/legal/{act_id}/delete": "POST удаление акта НПА",
    "/admin/legal/{act_id}/settings": "POST настройки акта",
    "/admin/server/status-fragment": "HTMX статус ops-agent",
    "/admin/server/redeploy-fragment": "HTMX лог деплоя",
    "/admin/server/agent-ping": "JSON health ops-agent",
    "/admin/server/logs": "POST логи контейнера",
    "/admin/server/restart": "POST restart (2FA)",
    "/admin/server/redeploy": "POST redeploy (2FA)",
    "/admin/server/backup": "POST внеплановый бэкап",
    "/admin/server/backup/download": "скачать архив бэкапа",
    "/admin/server/cert-renew": "POST reload Caddy",
    "/admin/server/hostland": "POST ссылки Hostland / дата VDS",
}

ROUTE_EXCEPTIONS: frozenset[str] = frozenset(ROUTE_EXCEPTION_NOTES)


def roles_for_user(
    *,
    is_service_admin: bool,
    has_org: bool,
    is_org_admin: bool = False,
) -> frozenset[NavRole]:
    """Собрать роли навигации. is_org_admin — users.org_role == org_admin."""
    if is_service_admin and not has_org:
        return frozenset({NavRole.service_admin})
    roles: set[NavRole] = set()
    if is_service_admin:
        roles.add(NavRole.service_admin)
    if has_org:
        roles.add(NavRole.org_user)
        if is_org_admin:
            roles.add(NavRole.org_admin)
    return frozenset(roles)


def tariff_allowed(item: NavItem, tariff: TariffCode | None) -> bool:
    if item.tariffs is None:
        return True
    if tariff is None:
        return False
    return tariff in item.tariffs


def role_allowed(item: NavItem, roles: frozenset[NavRole]) -> bool:
    return bool(item.roles & roles)


@dataclass
class ResolvedNavItem:
    item: NavItem
    allowed: bool
    upsell: bool
    upsell_tariff_label: str = ""

    @property
    def key(self) -> str:
        return self.item.key

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def url(self) -> str:
        if self.upsell:
            return "/cabinet/billing/"
        return self.item.url


def resolve_nav(
    *,
    roles: frozenset[NavRole],
    tariff: TariffCode | None,
    area: str | None = None,
    menu_only: bool = False,
    include_upsell: bool = True,
    include_quick: bool = False,
) -> list[ResolvedNavItem]:
    out: list[ResolvedNavItem] = []
    for item in NAV_REGISTRY:
        if area == "cabinet" and item.area not in ("cabinet",):
            continue
        if area == "admin" and item.area != "admin":
            continue
        if area == "search":
            # поиск: кабинет + public + quick + admin (если роль)
            if item.area == "admin" and NavRole.service_admin not in roles:
                continue
            if item.area == "cabinet" and not (roles & ORG_ROLES):
                continue
        if menu_only and not item.menu:
            continue
        if item.quick_action and not include_quick and menu_only:
            continue
        if not role_allowed(item, roles):
            continue
        if item.area in ("admin", "public"):
            allowed = True
        else:
            allowed = tariff_allowed(item, tariff)
        upsell = False
        label = ""
        if not allowed:
            if include_upsell and tariff == TariffCode.guest and item.tariffs:
                upsell = True
                if item.tariffs == ORG_TARIFF:
                    label = "Организация"
                elif item.tariffs == PAID:
                    label = "Специалист"
                else:
                    label = "платном тарифе"
            else:
                continue
        out.append(
            ResolvedNavItem(item=item, allowed=allowed, upsell=upsell, upsell_tariff_label=label)
        )
    return out


def cabinet_menu_tuples(
    *,
    roles: frozenset[NavRole],
    tariff: TariffCode | None,
) -> list[tuple[str, str, str]]:
    rows = resolve_nav(
        roles=roles, tariff=tariff, area="cabinet", menu_only=True, include_upsell=True
    )
    result: list[tuple[str, str, str]] = []
    for r in rows:
        label = r.title
        if r.upsell:
            label = f"{r.title} · тариф «{r.upsell_tariff_label}»"
        result.append((r.key, label, r.url))
    return result


def admin_menu_tuples() -> list[tuple[str, str, str]]:
    roles = frozenset({NavRole.service_admin})
    rows = resolve_nav(
        roles=roles, tariff=None, area="admin", menu_only=True, include_upsell=False
    )
    return [(r.key, r.title, r.item.url) for r in rows]


def match_registry(query: str, resolved: list[ResolvedNavItem]) -> list[ResolvedNavItem]:
    q = (query or "").strip().casefold()
    if len(q) < 2:
        return []
    hits: list[tuple[int, ResolvedNavItem]] = []
    for r in resolved:
        title = r.item.title.casefold()
        syns = [s.casefold() for s in r.item.synonyms]
        score = 0
        if q == title:
            score = 100
        elif title.startswith(q):
            score = 80
        elif q in title:
            score = 60
        elif any(q in s or s in q for s in syns):
            score = 50
        elif any(q in s for s in syns):
            score = 40
        if score:
            hits.append((score, r))
    hits.sort(key=lambda x: (-x[0], x[1].title))
    return [h for _, h in hits]


def sitemap_for(
    *,
    roles: frozenset[NavRole],
    tariff: TariffCode | None,
) -> list[tuple[str, list[ResolvedNavItem]]]:
    items = resolve_nav(
        roles=roles,
        tariff=tariff,
        area="search",
        menu_only=False,
        include_upsell=True,
        include_quick=True,
    )
    groups: dict[str, list[ResolvedNavItem]] = {}
    order: list[str] = []
    for r in items:
        g = "Быстрые действия" if r.item.quick_action else r.item.group
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(r)
    return [(g, groups[g]) for g in order]


def registry_urls() -> set[str]:
    """Канонические URL из реестра (без query)."""
    urls: set[str] = set()
    for item in NAV_REGISTRY:
        path = item.url.split("?", 1)[0]
        urls.add(path)
        if path.endswith("/") and path != "/":
            urls.add(path.rstrip("/"))
        elif not path.endswith("/"):
            urls.add(path + "/")
    return urls
