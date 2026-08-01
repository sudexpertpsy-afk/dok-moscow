"""
Логика мастера «Новый комплект» (без Tk).
"""

from pathlib import Path

from .filler import list_template_variables
from . import packages
from .contracts_registry import load_registry
from .utils import resolve_template_path, normalize_name

ТИПЫ = ('Физлицо', 'Юрлицо', 'Эксперт (ГПД)')

БЕЗ_ДОГОВОРА = 'Без договора'

# Запасной список; актуальный — contracts_registry.json в каталоге шаблонов.
_CONTRACTS = {
    'Физлицо': [
        'Договор_услуги_v2.docx',
        'Договор_услуги_с_печатью.docx',
        'Договор_освидетельствование.docx',
        'Договор_рецензия.docx',
        'Договор_обучение_СПЭ.docx',
        'Договор_обучение_полиграф.docx',
    ],
    'Юрлицо': [
        'Договор_услуги_юрлицо.docx',
        'Договор_рецензия_юрлицо.docx',
        'Договор_обучение_СПЭ_юрлицо.docx',
    ],
    'Эксперт (ГПД)': [
        'Договор_ГПД_эксперт.docx',
    ],
}

_CORE = {
    'Физлицо': [
        'фио_клиента', 'адрес_клиента', 'телефон_клиента', 'email_клиента',
    ],
    'Юрлицо': [
        'название_заказчика', 'инн_заказчика', 'кпп_заказчика', 'огрн_заказчика',
        'юр_адрес_заказчика', 'фио_подписанта', 'должность_подписанта_род',
        'основание_подписанта', 'телефон_клиента', 'email_клиента',
    ],
    'Эксперт (ГПД)': [
        'фио_эксперта', 'номер_договора', 'дата_договора', 'дата_начала',
        'дата_окончания', 'предмет_услуг', 'сумма',
    ],
}

_FIRST_FIELD = {
    'Физлицо': 'фио_клиента',
    'Юрлицо': 'название_заказчика',
    'Эксперт (ГПД)': 'фио_эксперта',
}


def core_fields(тип):
    return list(_CORE.get(тип, []))


def first_field(тип):
    return _FIRST_FIELD.get(тип, '')


def contract_options(тип, templates_dir):
    """Договоры типа + «Без договора» (только существующие файлы)."""
    templates_dir = Path(templates_dir)
    registry = load_registry(templates_dir)
    names = registry.get("contracts", {}).get(тип) or _CONTRACTS.get(тип, [])
    opts = []
    for name in names:
        if resolve_template_path(templates_dir, name).exists():
            opts.append(name)
    opts.append(БЕЗ_ДОГОВОРА)
    return opts


def extra_options(тип, contract_template):
    """Сопутствующие документы для выбранного договора."""
    if not contract_template or contract_template == БЕЗ_ДОГОВОРА:
        return []
    return packages.related_documents(contract_template)


def _vars_of_template(templates_dir, template_name):
    path = resolve_template_path(Path(templates_dir), template_name)
    if not path.exists():
        return set()
    try:
        return set(list_template_variables(path))
    except Exception:
        return set()


def collect_variables(templates_dir, selected_templates, тип):
    """Ядро и дополнительные переменные выбранного комплекта."""
    core = set(core_fields(тип))
    all_vars = set()
    for tpl in selected_templates or []:
        all_vars |= _vars_of_template(templates_dir, tpl)
    additional = sorted(all_vars - core)
    return sorted(core), additional


def selected_templates_list(contract_template, extra_checked):
    """Итоговый список шаблонов к генерации."""
    result = []
    if contract_template and contract_template != БЕЗ_ДОГОВОРА:
        result.append(contract_template)
    for name in extra_checked or []:
        if name not in result:
            result.append(name)
    return result


def build_contexts(templates_dir, selected, core_values, additional_values):
    """Контекст для каждого шаблона комплекта."""
    merged = {}
    merged.update(core_values or {})
    merged.update(additional_values or {})
    contexts = {}
    for tpl in selected:
        needed = _vars_of_template(templates_dir, tpl)
        ctx = {k: merged.get(k, '') for k in needed}
        contexts[tpl] = ctx
    return contexts


def infer_type_from_template(template_name):
    """Тип контрагента по имени шаблона договора."""
    name = normalize_name(str(template_name or ''))
    if name == 'Договор_ГПД_эксперт.docx':
        return 'Эксперт (ГПД)'
    if '_юрлицо' in name.lower():
        return 'Юрлицо'
    if name.startswith('Договор_'):
        return 'Физлицо'
    return 'Физлицо'


def package_field_defaults(contract_template, core_values, extra_templates):
    """Подсказки для полей пакета (счёт, акт, ПКО…)."""
    docs = [(n, True) for n in extra_templates]
    return packages.suggest_fields(contract_template, core_values, docs)


def display_name(template_name):
    return Path(template_name).stem.replace('_', ' ')
