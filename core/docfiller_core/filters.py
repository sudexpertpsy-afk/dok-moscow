"""
Умные функции (Jinja2-фильтры) для шаблонов документов.

В шаблоне Word можно писать:
    {{ "Лосев Сергей Александрович" | род }}          → Лосева Сергея Александровича
    {{ "Громинская Александра Владимировна" | дат }}  → Громинской Александре Владимировне
    {{ 250000 | прописью }}                            → двести пятьдесят тысяч
    {{ 250000 | руб }}                                 → 250 000 (двести пятьдесят тысяч) рублей
    {{ 30 | дней_прописью }}                           → тридцать
    {{ 30 | согласовать('день', 'дня', 'дней') }}      → 30 (тридцать) дней
    {{ 30 | раб_дней }}                                → 30 (тридцать) рабочих дней
    {{ настройки.исполнитель.должность | фраза | род }} → из словаря склонений
    {{ фио_клиента | фамилия | род }}                  → склонённая фамилия
    {{ "27.05.2026" | дата_рус }}                      → 27 мая 2026 г.
"""

from datetime import date, datetime
from petrovich.main import Petrovich
from petrovich.enums import Case, Gender
from num2words import num2words

_petrovich = Petrovich()
_declensions = {}

_CASE_MAP = {
    'род': Case.GENITIVE,
    'дат': Case.DATIVE,
    'вин': Case.ACCUSATIVE,
    'тв':  Case.INSTRUMENTAL,
    'пр':  Case.PREPOSITIONAL,
}

_MONTHS_RU = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]


def _parse_int(value):
    if value in (None, ''):
        return None
    try:
        return int(str(value).replace(' ', '').replace(',', '.').split('.')[0])
    except ValueError:
        return None


def _norm_phrase(text):
    return ' '.join(str(text).split())


def _plural_index(n):
    """Индекс формы слова: 0 — одна, 1 — две–четыре, 2 — пять+."""
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return 2
    r = n % 10
    if r == 1:
        return 0
    if r in (2, 3, 4):
        return 1
    return 2


def _detect_gender(fio_parts):
    """Определить пол по отчеству. Возвращает Gender.MALE / Gender.FEMALE / None."""
    if len(fio_parts) < 3:
        return None
    middle = fio_parts[2].lower()
    if middle.endswith(('ович', 'евич', 'ич')):
        return Gender.MALE
    if middle.endswith(('овна', 'евна', 'ична', 'инична')):
        return Gender.FEMALE
    return None


def _lookup_phrase(text, case_code):
    """Найти форму фразы в словаре склонений из настроек."""
    if not text or case_code == 'им':
        return None
    if not _declensions:
        return None
    key = _norm_phrase(text)
    entry = _declensions.get(key)
    if isinstance(entry, dict):
        form = entry.get(case_code)
        if form:
            return str(form)
    # Обратный поиск: если в форме уже вписали родительный падеж
    for base, forms in _declensions.items():
        if not isinstance(forms, dict):
            continue
        for code, form in forms.items():
            if _norm_phrase(form) == key and code == case_code:
                return str(form)
            if _norm_phrase(form) == key and case_code == 'им':
                return str(base)
    return None


def decline_word(word, case_code, gender=None):
    """Склонить одно слово (фамилию, имя…) через petrovich."""
    if not word or case_code == 'им':
        return str(word)
    case = _CASE_MAP.get(case_code)
    if case is None:
        return str(word)
    w = str(word).strip()
    # Одно слово трактуем как фамилию — типичный случай «| фамилия | род»
    return _petrovich.lastname(w, case, gender=gender)


def decline_fio(fio, case_code):
    """Просклонять ФИО (строка) в указанный падеж."""
    if not fio:
        return ''
    parts = str(fio).split()
    if not parts:
        return ''
    if case_code == 'им':
        return str(fio)
    phrase = _lookup_phrase(fio, case_code)
    if phrase is not None:
        return phrase
    case = _CASE_MAP.get(case_code)
    if case is None:
        return str(fio)
    gender = _detect_gender(parts)

    out = []
    if len(parts) >= 1:
        out.append(_petrovich.lastname(parts[0], case, gender=gender))
    if len(parts) >= 2:
        out.append(_petrovich.firstname(parts[1], case, gender=gender))
    if len(parts) >= 3:
        out.append(_petrovich.middlename(parts[2], case, gender=gender))
    return ' '.join(out)


def phrase_identity(value):
    """Маркер для цепочки «| фраза | род» — значение без изменений."""
    return '' if value is None else str(value)


def fio_part(fio, index):
    """Извлечь часть ФИО по индексу (0 — фамилия, 1 — имя, 2 — отчество)."""
    if not fio:
        return ''
    parts = str(fio).strip().split()
    if index < len(parts):
        return parts[index]
    return ''


def fio_surname(fio):
    return fio_part(fio, 0)


def fio_firstname(fio):
    return fio_part(fio, 1)


def fio_patronymic(fio):
    return fio_part(fio, 2)


def fio_gender_label(fio):
    """«мужской» / «женский» / пусто."""
    if not fio:
        return ''
    g = _detect_gender(str(fio).split())
    if g == Gender.MALE:
        return 'мужской'
    if g == Gender.FEMALE:
        return 'женский'
    return ''


def _decline_fio_part(fio, index, case_code):
    # Внутренний хелпер склонений для фильтров шаблонов.
    word = fio_part(fio, index)
    if not word:
        return ''
    gender = _detect_gender(str(fio).split())
    return decline_word(word, case_code, gender=gender)


def propisyu(amount):
    """Число прописью: 250000 → 'двести пятьдесят тысяч'."""
    if amount in (None, ''):
        return ''
    n = _parse_int(amount)
    if n is None:
        return str(amount)
    return num2words(n, lang='ru')


def days_in_words(amount):
    """Число прописью для сроков: 30 → 'тридцать'."""
    return propisyu(amount)


def agree_count(amount, form1, form2, form3, word_only=False):
    """Число + существительное: 30, 'день','дня','дней'."""
    if amount in (None, ''):
        return ''
    n = _parse_int(amount)
    if n is None:
        return str(amount)
    forms = (form1, form2, form3)
    word = forms[_plural_index(n)]
    if word_only:
        return word
    pretty = f"{n:,}".replace(',', ' ')
    words = num2words(n, lang='ru')
    return f"{pretty} ({words}) {word}"


def agree_count_filter(amount, form1, form2, form3, word_only=False):
    return agree_count(amount, form1, form2, form3, word_only=word_only)


def work_days_phrase(amount):
    """«30 (тридцать) рабочих дней»."""
    if amount in (None, ''):
        return ''
    n = _parse_int(amount)
    if n is None:
        return str(amount)
    adj = ('рабочий', 'рабочих', 'рабочих')[_plural_index(n)]
    day = ('день', 'дня', 'дней')[_plural_index(n)]
    pretty = f"{n:,}".replace(',', ' ')
    words = num2words(n, lang='ru')
    return f"{pretty} ({words}) {adj} {day}"


def calendar_days_phrase(amount):
    """«10 (десять) календарных дней»."""
    if amount in (None, ''):
        return ''
    n = _parse_int(amount)
    if n is None:
        return str(amount)
    adj = ('календарный', 'календарных', 'календарных')[_plural_index(n)]
    day = ('день', 'дня', 'дней')[_plural_index(n)]
    pretty = f"{n:,}".replace(',', ' ')
    words = num2words(n, lang='ru')
    return f"{pretty} ({words}) {adj} {day}"


def rub_format(amount):
    """Деньги в формате: '50 000 (пятьдесят тысяч) рублей'."""
    if amount in (None, ''):
        return ''
    n = _parse_int(amount)
    if n is None:
        return str(amount)
    pretty = f"{n:,}".replace(',', ' ')
    words = num2words(n, lang='ru')
    return f"{pretty} ({words}) рублей"


def kopeck_format(amount):
    """Деньги с копейками: 50000.50 → '50 000 (...) рублей 50 копеек'."""
    if amount in (None, ''):
        return ''
    try:
        s = str(amount).replace(' ', '').replace(',', '.')
        if '.' in s:
            rub, kop = s.split('.', 1)
            kop = (kop + '00')[:2]
        else:
            rub, kop = s, '00'
        rub_n = int(rub)
        kop_n = int(kop)
    except ValueError:
        return str(amount)
    pretty = f"{rub_n:,}".replace(',', ' ')
    words = num2words(rub_n, lang='ru')
    return f"{pretty} ({words}) рублей {kop_n:02d} копеек"


def date_ru(value):
    """Дата в формате '27 мая 2026 г.'."""
    if value in (None, ''):
        return ''
    d = None
    if isinstance(value, (datetime, date)):
        d = value
    else:
        s = str(value).strip()
        for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        if d is None:
            return s
    return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year} г."


def date_short(value):
    """Дата в формате 27.05.2026."""
    if value in (None, ''):
        return ''
    if isinstance(value, (datetime, date)):
        return value.strftime('%d.%m.%Y')
    return str(value)


def num_format(value):
    """Число с пробелами: 250000 → '250 000'."""
    if value in (None, ''):
        return ''
    n = _parse_int(value)
    if n is None:
        return str(value)
    return f"{n:,}".replace(',', ' ')


def initials(fio):
    """«Иванов Иван Иванович» → «И.И. Иванов»."""
    if not fio:
        return ''
    parts = str(fio).strip().split()
    if len(parts) >= 3:
        return f"{parts[1][0]}.{parts[2][0]}. {parts[0]}"
    if len(parts) == 2:
        return f"{parts[1][0]}. {parts[0]}"
    return str(fio)


def initials_after(fio):
    """«Иванов Иван Иванович» → «Иванов И.И.»."""
    if not fio:
        return ''
    parts = str(fio).strip().split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    if len(parts) == 2:
        return f"{parts[0]} {parts[1][0]}."
    return str(fio)


def install(jinja_env, declensions=None):
    """Зарегистрировать все фильтры в Jinja2-окружении docxtpl."""
    global _declensions
    _declensions = dict(declensions or {})

    for code in ('им', 'род', 'дат', 'вин', 'тв', 'пр'):
        jinja_env.filters[code] = lambda fio, c=code: decline_fio(fio, c)

    jinja_env.filters['фраза']         = phrase_identity
    jinja_env.filters['прописью']      = propisyu
    jinja_env.filters['дней_прописью'] = days_in_words
    jinja_env.filters['согласовать']   = agree_count_filter
    jinja_env.filters['раб_дней']      = work_days_phrase
    jinja_env.filters['кал_дней']      = calendar_days_phrase
    jinja_env.filters['руб']           = rub_format
    jinja_env.filters['руб_копейки']   = kopeck_format
    jinja_env.filters['дата_рус']      = date_ru
    jinja_env.filters['дата']          = date_short
    jinja_env.filters['число']         = num_format
    jinja_env.filters['ио']            = initials
    jinja_env.filters['фио_кратко']    = initials_after
    jinja_env.filters['фамилия']       = fio_surname
    jinja_env.filters['имя']           = fio_firstname
    jinja_env.filters['отчество']      = fio_patronymic
    jinja_env.filters['пол']           = fio_gender_label
