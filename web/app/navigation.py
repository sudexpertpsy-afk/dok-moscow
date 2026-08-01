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

    В модели пользователей пока нет отдельного org_admin —
    всем пользователям организации доступны и org_user, и org_admin.
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
        "billing",
        "Тариф и оплата",
        "/cabinet/billing/",
        synonyms=("оплата", "подписка", "тариф", "счёт", "карта"),
        group="Организация",
    ),
    _item(
        "settings",
        "Настройки",
        "/cabinet/settings/",
        synonyms=("реквизиты", "организация", "профиль"),
        roles=frozenset({NavRole.org_admin}),
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
        "settings_price",
        "Прайс",
        "/cabinet/settings/price",
        synonyms=("цены", "стоимость услуг"),
        roles=frozenset({NavRole.org_admin}),
        menu=False,
        group="Организация",
    ),
    _item(
        "settings_counters",
        "Счётчики номеров",
        "/cabinet/settings/counters",
        synonyms=("нумерация", "префикс", "номер договора"),
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
)


# Технические маршруты вне реестра навигации (инвентарный тест).
ROUTE_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "/",
        "/login",
        "/logout",
        "/forgot-password",
        "/reset-password/{token}",
        "/invite/{token}",
        "/apply",
        "/privacy",
        "/offer",
        "/requisites",
        "/tariffs",
        "/contacts",
        "/robots.txt",
        "/sitemap.xml",
        "/billing/webhook",
        "/api/global-search",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/cabinet/",
        "/cabinet/package",
        "/cabinet/counterparties",
        "/cabinet/settings",
        "/cabinet/org/{org_id}",
        "/cabinet/counterparties/suggest/party",
        "/cabinet/counterparties/suggest/address",
        "/cabinet/counterparties/suggest/bank",
        "/cabinet/counterparties/{cp_id}",
        "/cabinet/counterparties/{cp_id}/edit",
        "/cabinet/counterparties/{cp_id}/delete",
        "/cabinet/counterparties/new",
        "/cabinet/documents/new/{template_name}",
        "/cabinet/documents/{doc_id}",
        "/cabinet/documents/{doc_id}/download",
        "/cabinet/documents/{doc_id}/pdf",
        "/cabinet/package/step1",
        "/cabinet/package/step2",
        "/cabinet/package/step3",
        "/cabinet/package/done",
        "/cabinet/package/zip",
        "/cabinet/package/pdf",
        "/cabinet/package/reset",
        "/cabinet/package/repeat/{doc_id}",
        "/cabinet/templates/upload",
        "/cabinet/templates/rename",
        "/cabinet/templates/delete",
        "/cabinet/templates/role",
        "/cabinet/calendar/new",
        "/cabinet/calendar/{event_id}",
        "/cabinet/calendar/{event_id}/done",
        "/cabinet/calendar/{event_id}/delete",
        "/cabinet/billing/pay",
        "/cabinet/billing/success",
        "/cabinet/billing/fail",
        "/cabinet/settings/bank",
        "/cabinet/settings/signatories",
        "/cabinet/settings/price",
        "/cabinet/settings/counters",
        "/cabinet/settings/security",
        "/cabinet/party-check/search",
        "/cabinet/party-check/card",
        "/cabinet/party-check/select",
        "/cabinet/party-check/save",
        "/cabinet/party-check/pdf",
        "/cabinet/party-check/package",
        "/cabinet/party-check/journal/{check_id}",
        "/zakon/{slug}",
        "/admin/organizations",
        "/admin/organizations/{org_id}",
        "/admin/organizations/{org_id}/rename",
        "/admin/users/{user_id}/toggle",
        "/admin/leads/{lead_id}/invite",
        "/admin/invites",
        "/admin/invites/{invite_id}/revoke",
        "/admin/payments/export",
        "/admin/payments/{payment_id}",
        "/admin/payments/{payment_id}/reconcile",
        "/admin/payments/{payment_id}/mark-refund",
        "/admin/subscriptions/manual-extend",
        "/admin/payment-settings/test",
        "/admin/templates/upload",
        "/admin/templates/rename",
        "/admin/templates/delete",
        "/admin/templates/role",
    }
)


def roles_for_user(*, is_service_admin: bool, has_org: bool) -> frozenset[NavRole]:
    if is_service_admin and not has_org:
        return frozenset({NavRole.service_admin})
    if is_service_admin and has_org:
        return frozenset({NavRole.service_admin, NavRole.org_user, NavRole.org_admin})
    if has_org:
        return frozenset({NavRole.org_user, NavRole.org_admin})
    return frozenset()


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
