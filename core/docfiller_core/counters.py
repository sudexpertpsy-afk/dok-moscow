"""
Автоматическая нумерация документов.

Хранит в файле «.counters.json» в каталоге данных пользователя
(см. paths.py) последний использованный номер по каждому полю + шаблону.
При следующем заполнении формы программа предлагает следующий номер
по той же схеме.

Что считается «полем-номером»:
  • имя содержит «номер» (номер_договора, исх_номер, номер_дела…)

Как программа разбирает номер на части:
  '2026-001'      → префикс «2026-» + число 1  + ширина 3 + суффикс ''
  '127-С/26'      → префикс ''     + число 127 + ширина 3 + суффикс '-С/26'
  '№42/А'         → префикс '№'   + число 42  + ширина 2 + суффикс '/А'
  'А-001/26'      → префикс 'А-' + число 1   + ширина 3 + суффикс '/26'

Берётся ПЕРВАЯ группа цифр в строке — обычно это и есть тот номер,
который растёт от документа к документу. Остальное считается постоянным
шаблоном (например, год «/26» в исходящих письмах).

Программа НЕ подменяет введённое значение — она только предлагает.
Пользователь свободно может ввести любой номер вручную. После генерации
программа запоминает то, что в итоге было вписано.
"""

import json
import re

from . import paths


# Совместимость: тесты подменяют COUNTERS_PATH через monkeypatch.
COUNTERS_PATH = paths.counters_path()
APP_ROOT = paths.APP_ROOT


def _active_counters_path():
    return COUNTERS_PATH if COUNTERS_PATH is not None else paths.counters_path()


_DIGITS_RE = re.compile(r'\d+')
_MAX_NUMBER_LEN = 80


def is_number_field(name):
    """Поле похоже на номер документа?

    Срабатывает на:
        номер_договора, исх_номер, вх_номер, номер_дела, номер_доверенности,
        номер_рецензируемого
    """
    if not name:
        return False
    n = str(name).lower()
    return 'номер' in n


def _clean_number_text(value):
    """Убрать мусор (переносы, управляющие символы, слишком длинный хвост)."""
    if value is None:
        return ''
    s = str(value).replace('\r\n', '\n').replace('\r', '\n')
    s = s.split('\n', 1)[0].strip()
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    if len(s) > _MAX_NUMBER_LEN:
        s = s[:_MAX_NUMBER_LEN].strip()
    return s


def _group_weight(num_str):
    """Чем меньше вес — тем вероятнее это «увеличивающаяся» часть номера.

    4 цифры, начинается на 19/20  → год полный (1000)
    ровно 2 цифры                  → короткий год  (100)
    всё остальное                  → счётчик       (10)

    Эвристика: «настоящий счётчик документа меняется чаще года и в нём
    обычно либо 1, либо 3+ цифр». Не идеально, но покрывает все
    встретившиеся в реестрах форматы (2026-001, 127-С/26, №42/А, А-001/26).
    """
    if len(num_str) == 4 and num_str[:2] in ('19', '20'):
        return 1000
    if len(num_str) == 2:
        return 100
    return 10


def parse_number(value):
    """Разобрать строку с номером на (префикс, число, ширина, суффикс).

    Возвращает словарь или None, если ни одной цифры в строке нет.

    Внутри строки может быть несколько групп цифр (например, «2026-001»,
    где 2026 — год, 001 — собственно номер). Программа выбирает «лёгкую»
    группу (с наименьшим весом по эвристике _group_weight) — обычно это
    и есть тот номер, который инкрементируется. Остальной текст уходит
    в префикс/суффикс и сохраняется без изменений.
    """
    if value is None:
        return None
    s = _clean_number_text(value)
    if not s:
        return None
    matches = list(_DIGITS_RE.finditer(s))
    if not matches:
        return None
    # Выбираем группу с минимальным весом; при равенстве — первую по тексту
    best = min(matches, key=lambda m: (_group_weight(m.group()), m.start()))
    num_str = best.group()
    prefix = s[:best.start()]
    suffix = s[best.end():]
    # Защита: суффикс/префикс не должны раздуваться от вставленного мусора
    if len(prefix) > 40 or len(suffix) > 40:
        return {
            'prefix': '',
            'number': int(num_str),
            'width': len(num_str),
            'suffix': '',
        }
    return {
        'prefix': prefix,
        'number': int(num_str),
        'width':  len(num_str),
        'suffix': suffix,
    }


def format_number(parsed):
    """Собрать строку обратно по разобранным частям."""
    prefix = _clean_number_text(parsed.get('prefix', ''))
    suffix = _clean_number_text(parsed.get('suffix', ''))
    width = max(1, int(parsed.get('width') or 1))
    number = int(parsed['number'])
    return f"{prefix}{number:0{width}d}{suffix}"


def _sanitize_parsed(parsed):
    """Проверить запись счётчика; вернуть очищенную или None."""
    if not isinstance(parsed, dict):
        return None
    try:
        number = int(parsed['number'])
        width = int(parsed.get('width') or 1)
    except (KeyError, TypeError, ValueError):
        return None
    if number < 0 or width < 1 or width > 12:
        return None
    prefix = _clean_number_text(parsed.get('prefix', ''))
    suffix = _clean_number_text(parsed.get('suffix', ''))
    if len(prefix) > 40 or len(suffix) > 40:
        prefix, suffix = '', ''
    return {
        'prefix': prefix,
        'number': number,
        'width': width,
        'suffix': suffix,
    }


def _load():
    """Прочитать .counters.json. На любые ошибки — вернуть пустой словарь."""
    path = _active_counters_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding='utf-8') as f:
            raw = json.load(f) or {}
    except Exception:
        return {}
    cleaned = {}
    dirty = False
    for key, parsed in raw.items():
        fixed = _sanitize_parsed(parsed)
        if fixed is None:
            dirty = True
            continue
        if fixed != parsed:
            dirty = True
        cleaned[key] = fixed
    if dirty:
        _save(cleaned)
    return cleaned


def _save(data):
    """Записать .counters.json. Ошибки записи проглатываются — счётчики
    не должны блокировать генерацию документа."""
    try:
        path = _active_counters_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _key(template_name, field_name):
    """Ключ хранения: <шаблон>::<поле>. Для каждого шаблона — свой счётчик."""
    if template_name:
        return f"{template_name}::{field_name}"
    return field_name


def suggest_next(field_name, template_name=None):
    """Предложить следующий номер для поля. Возвращает строку или ''.

    Сначала ищем счётчик именно по паре (шаблон, поле). Если такого
    нет — пробуем общий счётчик по имени поля (для миграции, когда
    шаблон только что переименовали).
    """
    if not is_number_field(field_name):
        return ''
    data = _load()
    parsed = data.get(_key(template_name, field_name))
    if not parsed and field_name != 'номер_договора':
        # Общий счётчик — для миграции; для номер_договора не используем,
        # чтобы не смешивать форматы между шаблонами (T-9.3).
        parsed = data.get(field_name)
    if not parsed:
        return ''
    nxt = dict(parsed)
    nxt['number'] = parsed['number'] + 1
    return format_number(nxt)


def remember_use(field_name, value, template_name=None):
    """Запомнить использованный номер. Вызывать после успешной генерации.

    Обновляются два ключа:
      • <шаблон>::<поле>  — счётчик именно для этого шаблона
      • <поле>            — общий счётчик (если шаблонов несколько со схожей схемой)
    """
    if not is_number_field(field_name):
        return
    parsed = parse_number(value)
    if not parsed:
        return
    data = _load()
    data[_key(template_name, field_name)] = parsed
    data[field_name] = parsed
    _save(data)


def remember_use_many(context, template_name=None):
    """Пройти по всем полям контекста и запомнить те, что выглядят как номера."""
    for k, v in (context or {}).items():
        if is_number_field(k):
            remember_use(k, v, template_name=template_name)


def get_counters_for_template(template_name):
    """Вернуть {имя_поля: предложенный_следующий_номер} для всех полей-номеров,
    которые ранее встречались с этим шаблоном.

    Используется только для отладки/просмотра — основной поток идёт через
    suggest_next(field_name, template_name).
    """
    # TODO(этап 4+): vulture — нет внешних вызовов; оставить или удалить после ревью.
    data = _load()
    prefix = f"{template_name}::"
    result = {}
    for key, parsed in data.items():
        if key.startswith(prefix):
            field = key[len(prefix):]
            nxt = dict(parsed)
            nxt['number'] = parsed['number'] + 1
            result[field] = format_number(nxt)
    return result
