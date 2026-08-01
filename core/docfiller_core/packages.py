"""
Пакет документов к договору: счёт на оплату, акт оказанных услуг,
приходный кассовый ордер (унифицированная форма № КО-1).

Логика: после создания документа по шаблону «Договор_*» программа
предлагает сразу выписать связанные документы из тех же данных.
Номера счёта/акта/ПКО подсказываются счётчиками (модуль counters),
даты — сегодняшние, «Принято от» для ПКО подставляется в родительном
падеже. Все значения можно поправить в диалоге перед созданием.

Состав пакета можно переопределить в «настройки.yaml»:

    пакеты:
      'Договор_услуги_v2.docx':
        - 'Счёт_на_оплату.docx'
        - 'Акт_оказанных_услуг.docx'
        - 'ПКО_КО-1.docx'

Если раздел «пакеты» не задан, действует правило по умолчанию:
  Договор_*юрлицо*  → счёт-юрлицо, акт-юрлицо (+ ПКО, выключен по умолчанию —
                      юрлица обычно платят безналично; лимит наличных
                      расчётов между организациями — 100 000 руб. по договору)
  Договор_*         → счёт, акт, ПКО
"""

from datetime import date
from pathlib import Path

from . import counters
from . import filters
from .config import load_settings
from . import paths as app_paths
from .registry import package_name, self_contained_set


# Шаблоны пакета (значения по умолчанию; переопределяются реестром)
BILL_FIZ = 'Счёт_на_оплату.docx'
BILL_JUR = 'Счёт_на_оплату_юрлицо.docx'
ACT_FIZ  = 'Акт_оказанных_услуг.docx'
ACT_JUR  = 'Акт_оказанных_услуг_юрлицо.docx'
PKO      = 'ПКО_КО-1.docx'
GPD_CONTRACT = 'Договор_ГПД_эксперт.docx'
ACT_GPD  = 'Акт_ГПД_эксперт.docx'
CONSENT  = 'Согласие_ПДн.docx'
SELF_CONTAINED_CONTRACTS = {
    # В шаблоне уже находятся счёт, акт и форма ПКО КО-1.
    'Договор_освидетельствование.docx',
}


def _templates_root(settings=None):
    if settings is not None:
        root = getattr(settings, 'templates_dir', None)
        if root:
            return Path(root)
        if isinstance(settings, dict) and settings.get('templates_dir'):
            return Path(settings['templates_dir'])
    return Path(app_paths.TEMPLATES_DIR)


def _pkg(templates_dir, key, fallback):
    try:
        return package_name(templates_dir, key)
    except Exception:
        return fallback

# Наименование услуги по ключевому слову в имени шаблона договора —
# используется, когда в договоре нет поля «предмет_договора».
_SERVICE_BY_TEMPLATE = (
    ('рецензия',  'Составление заключения специалиста (рецензии) на заключение '
                  'судебной экспертизы'),
    ('обучение_спэ', 'Услуги по обучению по дополнительной профессиональной '
                     'программе в области судебной психологической экспертизы'),
    ('обучение_полиграф', 'Услуги по обучению в области специальных '
                          'психофизиологических исследований с применением полиграфа'),
    ('освидетельствование', 'Комплексное медико-психолого-психиатрическое '
                            'освидетельствование'),
    ('услуги',    'Оказание экспертных услуг по договору'),
)


def _is_jur(template_name):
    n = str(template_name).lower()
    return '_юрлицо' in n or '_юл' in n


def related_documents(template_name, settings=None, templates_dir=None):
    """Список документов пакета для шаблона договора.

    Возвращает список пар (имя_шаблона, включён_по_умолчанию).
    Пустой список — пакет не предусмотрен (шаблон не договор).
    settings — объект/словарь настроек (веб); иначе load_settings().
    templates_dir — каталог шаблонов (веб передаёт явно).
    """
    name = str(template_name or '')
    if not name.startswith('Договор_'):
        return []

    if settings is None:
        settings = load_settings()
    elif isinstance(settings, dict):
        from .config import settings_from_dict
        settings = settings_from_dict(settings)

    templates_dir = Path(templates_dir) if templates_dir else _templates_root(settings)
    # Переопределение из настроек, если задано
    custom = settings.get('пакеты') or {}
    if name in custom:
        docs = custom[name] or []
        return [(str(d), True) for d in docs]
    if name in self_contained_set(templates_dir) or name in SELF_CONTAINED_CONTRACTS:
        return []

    gpd = _pkg(templates_dir, 'gpd_contract', GPD_CONTRACT)
    if name == gpd:
        return [
            (_pkg(templates_dir, 'act_gpd', ACT_GPD), True),
            (_pkg(templates_dir, 'consent', CONSENT), True),
        ]

    if _is_jur(name):
        return [
            (_pkg(templates_dir, 'bill_jur', BILL_JUR), True),
            (_pkg(templates_dir, 'act_jur', ACT_JUR), True),
            (_pkg(templates_dir, 'pko', PKO), False),
        ]
    return [
        (_pkg(templates_dir, 'bill_fiz', BILL_FIZ), True),
        (_pkg(templates_dir, 'act_fiz', ACT_FIZ), True),
        (_pkg(templates_dir, 'pko', PKO), True),
    ]


def default_service_name(template_name, contract_context):
    """Наименование услуги для счёта/акта: из предмета договора либо по типу шаблона."""
    subject = (contract_context or {}).get('предмет_договора')
    if subject:
        return str(subject)
    n = str(template_name).lower()
    for key, text in _SERVICE_BY_TEMPLATE:
        if key in n:
            return text
    return ''


def _payer(contract_context):
    """Кто платит: название организации либо ФИО клиента."""
    c = contract_context or {}
    return str(c.get('название_заказчика') or c.get('фио_клиента') or '')


def suggest_fields(contract_template, contract_context, docs):
    """Подсказки значений общих полей пакета.

    Возвращает словарь {имя_поля: значение} для показа в диалоге:
    номера — из счётчиков (иначе — номер договора), даты — сегодня,
    «принято_от» — плательщик в родительном падеже (для ФИО).
    """
    c = contract_context or {}
    today = date.today().strftime('%d.%m.%Y')
    contract_no = str(c.get('номер_договора') or '')
    fields = {}
    doc_names = {name for name, _ in docs}

    if BILL_FIZ in doc_names or BILL_JUR in doc_names:
        fields['номер_счёта'] = (counters.suggest_next('номер_счёта') or contract_no)
        fields['дата_счёта'] = today
    if ACT_FIZ in doc_names or ACT_JUR in doc_names:
        fields['номер_акта'] = (counters.suggest_next('номер_акта') or contract_no)
        fields['дата_акта'] = str(c.get('дата_акта') or today)
    if PKO in doc_names:
        # Нумерация ПКО непрерывна в течение года и начинается заново
        # с нового года; если счётчика ещё нет — первый номер «1».
        fields['номер_пко'] = counters.suggest_next('номер_пко') or '1'
        fields['дата_пко'] = today
        payer = _payer(c)
        if payer and not c.get('название_заказчика'):
            payer = filters.decline_fio(payer, 'род')  # «Принято от Иванова Петра…»
        fields['принято_от'] = payer
        fields['основание_пко'] = (
            f'Оплата по договору № {contract_no} от {c.get("дата_договора", "")}'
            if contract_no else 'Оплата по договору'
        )
        fields['приложение_пко'] = '—'

    fields['наименование_услуги'] = default_service_name(contract_template, c)

    if str(contract_template) == GPD_CONTRACT:
        fields['номер_акта'] = (
            counters.suggest_next('номер_акта', template_name=ACT_GPD) or contract_no
        )
        fields['дата_акта'] = today
        fields['фио_субъекта'] = str(c.get('фио_эксперта') or '')
        fields['паспорт_субъекта'] = str(c.get('паспорт_эксперта') or '')
        fields['адрес_субъекта'] = str(c.get('адрес_эксперта') or '')
        fields['цели_обработки'] = (
            'заключение и исполнение гражданско-правового договора, '
            'ведение кадрового и бухгалтерского учёта'
        )

    return fields


def build_context(doc_template, contract_context, common_fields):
    """Итоговый контекст для генерации документа пакета:
    данные договора + общие поля пакета (номера, даты, наименование услуги)."""
    ctx = dict(contract_context or {})
    ctx.update(common_fields or {})
    return ctx


def number_field_of(doc_template):
    """Имя поля-номера конкретного документа пакета (для имени файла и счётчика)."""
    n = str(doc_template)
    if n.startswith('Счёт_'):
        return 'номер_счёта'
    if n.startswith('Акт_'):
        return 'номер_акта'
    if n.startswith('ПКО'):
        return 'номер_пко'
    return ''
