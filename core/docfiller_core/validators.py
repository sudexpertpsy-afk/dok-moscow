"""
Валидация реквизитов (WP-04).

Ошибки блокируют генерацию; предупреждения — только по подтверждению.
ИНН с неверной контрольной суммой — всегда ошибка (не предупреждение).
"""

from __future__ import annotations

import re
from datetime import date, datetime


class ValidationIssue:
    """Результат проверки одного поля."""

    __slots__ = ('field', 'message', 'level')

    def __init__(self, field, message, level='error'):
        self.field = field
        self.message = message
        self.level = level  # 'error' | 'warning'

    def __repr__(self):
        return f'ValidationIssue({self.field!r}, {self.message!r}, {self.level!r})'

    def __eq__(self, other):
        return (
            isinstance(other, ValidationIssue)
            and self.field == other.field
            and self.message == other.message
            and self.level == other.level
        )


def _digits(value):
    return re.sub(r'\D', '', str(value or ''))


# --- ИНН -----------------------------------------------------------------

_INN10_W = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_W1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_W2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _inn_check_digit(nums, weights):
    s = sum(n * w for n, w in zip(nums, weights))
    return (s % 11) % 10


def validate_inn(value):
    """Проверить ИНН 10 (юрлицо) или 12 (физлицо) знаков."""
    d = _digits(value)
    if not d:
        return ValidationIssue('инн', 'Укажите ИНН', 'error')
    if len(d) not in (10, 12) or not d.isdigit():
        return ValidationIssue('инн', 'ИНН должен содержать 10 или 12 цифр', 'error')
    nums = [int(c) for c in d]
    if len(d) == 10:
        if _inn_check_digit(nums[:9], _INN10_W) != nums[9]:
            return ValidationIssue('инн', 'Неверная контрольная сумма ИНН', 'error')
    else:
        if _inn_check_digit(nums[:10], _INN12_W1) != nums[10]:
            return ValidationIssue('инн', 'Неверная контрольная сумма ИНН', 'error')
        if _inn_check_digit(nums[:11], _INN12_W2) != nums[11]:
            return ValidationIssue('инн', 'Неверная контрольная сумма ИНН', 'error')
    return None


# --- ОГРН / ОГРНИП -------------------------------------------------------

def validate_ogrn(value):
    """ОГРН (13) или ОГРНИП (15)."""
    d = _digits(value)
    if not d:
        return ValidationIssue('огрн', 'Укажите ОГРН/ОГРНИП', 'error')
    if len(d) not in (13, 15) or not d.isdigit():
        return ValidationIssue('огрн', 'ОГРН — 13 цифр, ОГРНИП — 15', 'error')
    if len(d) == 13:
        base, check = int(d[:12]), int(d[12])
        if base % 11 % 10 != check:
            return ValidationIssue('огрн', 'Неверная контрольная сумма ОГРН', 'error')
    else:
        base, check = int(d[:14]), int(d[14])
        if base % 13 % 10 != check:
            return ValidationIssue('огрн', 'Неверная контрольная сумма ОГРНИП', 'error')
    return None


# --- СНИЛС ---------------------------------------------------------------

def validate_snils(value):
    d = _digits(value)
    if not d:
        return ValidationIssue('снилс', 'Укажите СНИЛС', 'error')
    if len(d) != 11 or not d.isdigit():
        return ValidationIssue('снилс', 'СНИЛС должен содержать 11 цифр', 'error')
    nums = [int(c) for c in d[:9]]
    checksum = int(d[9:11])
    if nums == list(range(1, 10)) or len(set(nums)) == 1:
        # эвристика: очевидно тестовые — всё равно считаем КС
        pass
    total = sum(n * (9 - i) for i, n in enumerate(nums))
    if total < 100:
        expect = total
    elif total in (100, 101):
        expect = 0
    else:
        expect = total % 101
        if expect in (100, 101):
            expect = 0
    if expect != checksum:
        return ValidationIssue('снилс', 'Неверная контрольная сумма СНИЛС', 'error')
    return None


# --- КПП / БИК / счета ---------------------------------------------------

def validate_kpp(value):
    d = _digits(value)
    if not d:
        return ValidationIssue('кпп', 'Укажите КПП', 'error')
    if not re.fullmatch(r'\d{9}', d):
        return ValidationIssue('кпп', 'КПП должен содержать 9 цифр', 'error')
    return None


def validate_bik(value):
    d = _digits(value)
    if not d:
        return ValidationIssue('бик', 'Укажите БИК', 'error')
    if not re.fullmatch(r'\d{9}', d):
        return ValidationIssue('бик', 'БИК должен содержать 9 цифр', 'error')
    return None


def validate_account(value, bik, *, field='р_счёт'):
    """Ключевание лицевого счёта по БИК (Положение ЦБ о расчёте контрольного ключа).

    - счёт в кредитной организации: 3 последних цифры БИК + 20 цифр счёта;
    - счёт в РКЦ (БИК оканчивается на 000): «0» + 5–6 разряды БИК + счёт;
    - корреспондентский счёт КО (301…): «0» + 3 последних цифры БИК + счёт.
    """
    acc = _digits(value)
    b = _digits(bik)
    if not acc:
        return ValidationIssue(field, 'Укажите номер счёта', 'error')
    if len(acc) != 20 or not acc.isdigit():
        return ValidationIssue(field, 'Счёт должен содержать 20 цифр', 'error')
    if validate_bik(b) is not None:
        return ValidationIssue(field, 'Для проверки счёта нужен корректный БИК', 'error')

    # Коррсчёт кредитной организации в РКЦ / Банке России
    if acc.startswith('301'):
        if b.endswith('000'):
            prefix = '0' + b[4:6]
        else:
            prefix = '0' + b[-3:]
    elif b.endswith('000'):
        # Лицевой счёт, открытый в РКЦ
        prefix = '0' + b[4:6]
    else:
        # Расчётный (и иной) счёт клиента в кредитной организации
        prefix = b[-3:]

    check_str = prefix + acc
    weights = (7, 1, 3)
    total = sum(int(ch) * weights[i % 3] for i, ch in enumerate(check_str))
    if total % 10 != 0:
        return ValidationIssue(field, 'Счёт не соответствует БИК (ключевание)', 'error')
    return None


# --- Паспорт -------------------------------------------------------------

def validate_passport(series, number):
    s = _digits(series)
    n = _digits(number)
    issues = []
    if not re.fullmatch(r'\d{4}', s or ''):
        issues.append(ValidationIssue('паспорт_серия', 'Серия паспорта — 4 цифры', 'error'))
    if not re.fullmatch(r'\d{6}', n or ''):
        issues.append(ValidationIssue('паспорт_номер', 'Номер паспорта — 6 цифр', 'error'))
    return issues


# --- Даты ----------------------------------------------------------------

_DATE_FMTS = ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y')


def parse_date(value):
    s = str(value or '').strip()
    if not s:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return False  # непустая, но не распарсилась


def validate_date(value, field='дата', *, allow_empty=True):
    if not str(value or '').strip():
        if allow_empty:
            return None
        return ValidationIssue(field, 'Укажите дату', 'error')
    parsed = parse_date(value)
    if parsed is False:
        return ValidationIssue(field, 'Некорректная дата', 'error')
    today = date.today()
    if parsed > today.replace(year=today.year + 5):
        return ValidationIssue(field, 'Дата слишком далеко в будущем', 'warning')
    if parsed.year < 1990:
        return ValidationIssue(field, 'Дата слишком далеко в прошлом', 'warning')
    return None


def validate_date_range(start, end, start_field='дата_начала', end_field='дата_окончания'):
    issues = []
    a = parse_date(start)
    b = parse_date(end)
    if a is False:
        issues.append(ValidationIssue(start_field, 'Некорректная дата начала', 'error'))
    if b is False:
        issues.append(ValidationIssue(end_field, 'Некорректная дата окончания', 'error'))
    if isinstance(a, date) and isinstance(b, date) and b < a:
        issues.append(ValidationIssue(
            end_field, 'Дата окончания раньше даты начала', 'error',
        ))
    return issues


# --- Телефон / e-mail / сумма --------------------------------------------

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def validate_phone(value):
    d = _digits(value)
    if not d:
        return ValidationIssue('телефон', 'Укажите телефон', 'error')
    if len(d) not in (10, 11):
        return ValidationIssue('телефон', 'Телефон: 10 или 11 цифр', 'error')
    if len(d) == 11 and d[0] not in '78':
        return ValidationIssue('телефон', 'Телефон должен начинаться с 7 или 8', 'error')
    return None


def validate_email(value):
    s = str(value or '').strip()
    if not s:
        return ValidationIssue('email', 'Укажите e-mail', 'error')
    if not _EMAIL_RE.match(s) or '..' in s:
        return ValidationIssue('email', 'Некорректный e-mail', 'error')
    return None


def validate_amount(value, field='сумма'):
    s = str(value or '').strip().replace(' ', '').replace(',', '.')
    if not s:
        return ValidationIssue(field, 'Укажите сумму', 'error')
    try:
        amount = float(s)
    except ValueError:
        return ValidationIssue(field, 'Сумма должна быть числом', 'error')
    if amount < 0:
        return ValidationIssue(field, 'Сумма не может быть отрицательной', 'error')
    if amount == 0:
        return ValidationIssue(field, 'Сумма равна нулю', 'warning')
    return None


# --- Сводная проверка контекста ------------------------------------------

_FIELD_VALIDATORS = {
    'инн': validate_inn,
    'инн_заказчика': validate_inn,
    'огрн': validate_ogrn,
    'снилс': validate_snils,
    'кпп': validate_kpp,
    'кпп_заказчика': validate_kpp,
    'бик': validate_bik,
    'телефон': validate_phone,
    'телефон_клиента': validate_phone,
    'email': validate_email,
    'email_клиента': validate_email,
    'сумма': validate_amount,
}


def validate_context(context, *, required=None):
    """Проверить заполненный контекст формы.

    required — iterable имён полей, которые нельзя оставлять пустыми.
    Возвращает список ValidationIssue.
    """
    ctx = context or {}
    issues = []
    required = set(required or [])

    for name in required:
        if not str(ctx.get(name) or '').strip():
            issues.append(ValidationIssue(name, 'Обязательное поле', 'error'))

    for key, validator in _FIELD_VALIDATORS.items():
        val = ctx.get(key)
        if val in (None, ''):
            continue
        issue = validator(val)
        if issue:
            issues.append(ValidationIssue(key, issue.message, issue.level))

    # Счёт + БИК
    bik = ctx.get('бик')
    for acc_key in ('р_счёт', 'расчётный_счёт', 'корр_счёт'):
        if ctx.get(acc_key):
            issue = validate_account(ctx.get(acc_key), bik, field=acc_key)
            if issue:
                issues.append(issue)

    if ctx.get('паспорт_серия') or ctx.get('паспорт_номер'):
        issues.extend(validate_passport(
            ctx.get('паспорт_серия'), ctx.get('паспорт_номер'),
        ))

    for key, val in ctx.items():
        if key.startswith('дата') and val not in (None, ''):
            issue = validate_date(val, field=key)
            if issue:
                issues.append(issue)

    if ctx.get('дата_договора') and ctx.get('дата_окончания'):
        issues.extend(validate_date_range(
            ctx.get('дата_договора'), ctx.get('дата_окончания'),
            'дата_договора', 'дата_окончания',
        ))

    return issues


def has_errors(issues):
    return any(i.level == 'error' for i in issues)


def has_warnings(issues):
    return any(i.level == 'warning' for i in issues)
