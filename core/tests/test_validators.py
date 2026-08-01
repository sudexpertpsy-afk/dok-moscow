"""Тесты валидации реквизитов (WP-04) — не менее 40 кейсов."""

import pytest

from docfiller_core import validators as v


VALID_INN10 = '7707083893'
VALID_INN12 = '500100732259'
VALID_OGRN = '1027700132195'
VALID_SNILS = '11223344595'
VALID_BIK = '044525225'
VALID_ACCOUNT = '40817810200000000006'  # ключевание: last3(БИК)+счёт
# Реальный р/с Альфа-Банка (БИК 044525593) — раньше ложно отвергался из‑за «0»+prefix
ALFA_BIK = '044525593'
ALFA_ACCOUNT = '40702810702320001422'
ALFA_CORR = '30101810200000000593'


@pytest.mark.parametrize('value,ok', [
    (VALID_INN10, True),
    (VALID_INN12, True),
    ('7707083894', False),       # битая КС
    ('123', False),
    ('', False),
    ('abcdefghij', False),
    ('500100732258', False),     # битый 12
])
def test_inn_cases(value, ok):
    issue = v.validate_inn(value)
    assert (issue is None) is ok
    if not ok and value and value.isdigit() and len(value) in (10, 12):
        assert 'контрол' in issue.message.lower() or 'цифр' in issue.message.lower()


@pytest.mark.parametrize('value,ok', [
    (VALID_OGRN, True),
    ('1027700132196', False),
    ('123', False),
    ('', False),
])
def test_ogrn_cases(value, ok):
    assert (v.validate_ogrn(value) is None) is ok


@pytest.mark.parametrize('value,ok', [
    (VALID_SNILS, True),
    ('112-233-445 95', True),
    ('11223344596', False),
    ('123', False),
])
def test_snils_cases(value, ok):
    assert (v.validate_snils(value) is None) is ok


@pytest.mark.parametrize('value,ok', [
    ('773601001', True),
    ('123', False),
    ('', False),
])
def test_kpp_cases(value, ok):
    assert (v.validate_kpp(value) is None) is ok


@pytest.mark.parametrize('value,ok', [
    (VALID_BIK, True),
    ('04452522', False),
    ('', False),
])
def test_bik_cases(value, ok):
    assert (v.validate_bik(value) is None) is ok


@pytest.mark.parametrize('acc,bik,ok', [
    (VALID_ACCOUNT, VALID_BIK, True),
    (ALFA_ACCOUNT, ALFA_BIK, True),
    (ALFA_CORR, ALFA_BIK, True),
    ('40817810000000000007', VALID_BIK, False),
    ('123', VALID_BIK, False),
    (VALID_ACCOUNT, '000000000', False),
    # старый неверный алгоритм («0»+last3) принимал этот счёт — теперь нет
    ('40817810000000000006', VALID_BIK, False),
])
def test_account_cases(acc, bik, ok):
    assert (v.validate_account(acc, bik) is None) is ok


@pytest.mark.parametrize('series,number,ok', [
    ('4510', '123456', True),
    ('45 10', '123456', True),
    ('451', '123456', False),
    ('4510', '12345', False),
])
def test_passport_cases(series, number, ok):
    issues = v.validate_passport(series, number)
    assert (not issues) is ok


@pytest.mark.parametrize('value,ok,level', [
    ('30.07.2026', True, None),
    ('2026-07-30', True, None),
    ('32.13.2026', False, 'error'),
    ('01.01.1980', True, 'warning'),  # далеко в прошлом — warning
    ('', True, None),
])
def test_date_cases(value, ok, level):
    issue = v.validate_date(value)
    if ok and level is None:
        assert issue is None or issue.level == 'warning'
    if level == 'error':
        assert issue is not None and issue.level == 'error'
    if level == 'warning':
        assert issue is not None and issue.level == 'warning'


def test_date_range():
    issues = v.validate_date_range('01.02.2026', '01.01.2026')
    assert any(i.level == 'error' for i in issues)
    assert v.validate_date_range('01.01.2026', '01.02.2026') == []


@pytest.mark.parametrize('value,ok', [
    ('+7 916 123-45-67', True),
    ('89161234567', True),
    ('123', False),
    ('', False),
])
def test_phone_cases(value, ok):
    assert (v.validate_phone(value) is None) is ok


@pytest.mark.parametrize('value,ok', [
    ('user@example.com', True),
    ('a@b.c', True),
    ('not-an-email', False),
    ('a@@b.com', False),
    ('', False),
])
def test_email_cases(value, ok):
    assert (v.validate_email(value) is None) is ok


@pytest.mark.parametrize('value,ok,level', [
    ('1000', True, None),
    ('1 000,50', True, None),
    ('-5', False, 'error'),
    ('0', True, 'warning'),
    ('abc', False, 'error'),
])
def test_amount_cases(value, ok, level):
    issue = v.validate_amount(value)
    if ok and level is None:
        assert issue is None
    elif level == 'warning':
        assert issue and issue.level == 'warning'
    elif level == 'error':
        assert issue and issue.level == 'error'


def test_context_bad_inn_is_error_not_warning():
    issues = v.validate_context({'инн': '7707083894'})
    assert v.has_errors(issues)
    assert not any(i.level == 'warning' for i in issues if i.field == 'инн')


def test_context_required_and_mixed():
    issues = v.validate_context(
        {'email_клиента': 'bad', 'сумма': '10'},
        required=['фио_клиента'],
    )
    assert any(i.field == 'фио_клиента' for i in issues)
    assert any(i.field == 'email_клиента' for i in issues)
