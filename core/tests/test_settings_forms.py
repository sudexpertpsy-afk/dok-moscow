"""Тесты сохранения настроек через формы (WP-07)."""

from docfiller_core import config


def test_save_settings_dict_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / 'настройки.yaml'
    monkeypatch.setattr(config, 'CONFIG_PATH', path)

    data = {
        'организация': {
            'короткое_название': 'ООО Тест',
            'инн': '7707083893',
            'email': 'a@b.ru',
        },
        'банк': {
            'бик': '044525225',
            'расчётный_счёт': '40817810200000000006',
        },
        'прайс': {'сппэ': '54000', 'новая': '1000'},
        'константы': {'срок_дней_по_умолчанию': '14'},
        'исполнитель': {'фио': 'Иванов И.И.'},
        'бухгалтер': {'фио': 'Петров'},
        'кассир': {'фио': 'Петров'},
    }
    err = config.save_settings_dict(data)
    assert err == ''
    assert path.exists()

    loaded = config.load_settings_dict()
    assert loaded['организация']['короткое_название'] == 'ООО Тест'
    assert loaded['организация']['инн'] == '7707083893'
    assert int(loaded['прайс']['сппэ']) == 54000
    assert int(loaded['прайс']['новая']) == 1000
    assert int(loaded['константы']['срок_дней_по_умолчанию']) == 14


def test_save_settings_yaml_rejects_list(tmp_path, monkeypatch):
    path = tmp_path / 'настройки.yaml'
    monkeypatch.setattr(config, 'CONFIG_PATH', path)
    err = config.save_settings_yaml('- a\n- b\n')
    assert err
