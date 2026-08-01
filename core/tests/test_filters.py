"""Тесты Jinja2-фильтров (T-11)."""

from docfiller_core import filters


def test_decline_fio_male_genitive():
    assert filters.decline_fio('Иванов Иван Иванович', 'род') == (
        'Иванова Ивана Ивановича'
    )


def test_decline_fio_female_dative():
    assert filters.decline_fio('Иванова Мария Петровна', 'дат') == (
        'Ивановой Марии Петровне'
    )


def test_rub_format():
    assert filters.rub_format(54000) == '54 000 (пятьдесят четыре тысячи) рублей'


def test_rub_empty():
    assert filters.rub_format('') == ''


def test_days_in_words():
    assert filters.days_in_words(30) == 'тридцать'


def test_agree_count():
    assert filters.agree_count(1, 'день', 'дня', 'дней') == '1 (один) день'
    assert filters.agree_count(2, 'день', 'дня', 'дней') == '2 (два) дня'
    assert filters.agree_count(5, 'день', 'дня', 'дней') == '5 (пять) дней'
    assert filters.agree_count(21, 'день', 'дня', 'дней') == '21 (двадцать один) день'
    assert filters.agree_count(22, 'день', 'дня', 'дней') == '22 (двадцать два) дня'


def test_work_days_phrase():
    assert 'рабочих дней' in filters.work_days_phrase(30)
    assert 'рабочий день' in filters.work_days_phrase(1)


def test_phrase_declension_from_dict():
    decl = {
        'Генеральный директор': {
            'род': 'Генерального директора',
        },
    }
    filters.install(__import__('jinja2').Environment(), declensions=decl)
    assert filters.decline_fio('Генеральный директор', 'род') == (
        'Генерального директора'
    )


def test_fio_parts():
    fio = 'Иванова Мария Петровна'
    assert filters.fio_surname(fio) == 'Иванова'
    assert filters.fio_firstname(fio) == 'Мария'
    assert filters.fio_patronymic(fio) == 'Петровна'
    assert filters.fio_gender_label(fio) == 'женский'
