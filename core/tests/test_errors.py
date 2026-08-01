"""Тесты журнала ошибок (WP-06)."""

from docfiller_core import errors as errorlog


def test_mask_pdn_email_phone_inn():
    text = 'клиент Иван, email user@mail.ru, тел +7 916 111-22-33, инн: 7707083893'
    masked = errorlog.mask_pdn(text)
    assert 'user@mail.ru' not in masked
    assert '+7 916 111-22-33' not in masked
    assert '7707083893' not in masked
    assert '[email]' in masked
    assert '[телефон]' in masked
    assert '***' in masked or '[инн]' in masked


def test_mask_context():
    ctx = errorlog.mask_context({
        'фио_клиента': 'Иванов',
        'номер_договора': 'У-1',
        'сумма': 100,
    })
    assert ctx['фио_клиента'] == '***'
    assert ctx['номер_договора'] == 'У-1'


def test_log_exception_no_pdn(tmp_path, monkeypatch):
    monkeypatch.setattr(errorlog, '_configured', False)
    monkeypatch.setattr(errorlog, 'logs_dir', lambda: tmp_path)
    monkeypatch.setattr(errorlog, 'log_file_path', lambda: tmp_path / 'шаблонер.log')
    errorlog.setup_logging()
    try:
        raise RuntimeError('сбой для фио_клиента=Секретный')
    except RuntimeError as exc:
        errorlog.log_exception(exc, context={'фио_клиента': 'Секретный'})
    text = (tmp_path / 'шаблонер.log').read_text(encoding='utf-8')
    assert 'RuntimeError' in text
    assert 'Секретный' not in text
