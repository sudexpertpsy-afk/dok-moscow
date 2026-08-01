"""Тесты автобэкапа (WP-05)."""

import zipfile
from pathlib import Path

from docfiller_core import backup, paths


def test_backup_create_rotate_restore(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    backups = data / 'backups'
    templates = tmp_path / 'Шаблоны'
    data.mkdir()
    backups.mkdir()
    templates.mkdir()
    (templates / 'demo.docx').write_bytes(b'PK\x03\x04demo')

    monkeypatch.setattr(paths, 'data_dir', lambda: data)
    monkeypatch.setattr(paths, 'backups_dir', lambda: backups)
    monkeypatch.setattr(paths, 'db_path', lambda: data / 'shabloner.sqlite')
    monkeypatch.setattr(paths, 'config_path', lambda: data / 'настройки.yaml')
    monkeypatch.setattr(paths, 'journal_path', lambda: data / '.journal.jsonl')
    monkeypatch.setattr(paths, 'counters_path', lambda: data / '.counters.json')
    monkeypatch.setattr(paths, 'TEMPLATES_DIR', templates)
    monkeypatch.setattr(backup, 'DAILY_KEEP', 3)
    monkeypatch.setattr(backup, 'MONTHLY_KEEP', 2)

    (data / 'shabloner.sqlite').write_bytes(b'sqlite-demo')
    (data / 'настройки.yaml').write_text('организация:\n  инн: "1"\n', encoding='utf-8')

    a1 = backup.create_backup(force=True, kind='daily')
    assert a1 and a1.exists()
    with zipfile.ZipFile(a1) as zf:
        names = zf.namelist()
        assert 'data/shabloner.sqlite' in names
        assert 'data/настройки.yaml' in names
        assert any(n.startswith('Шаблоны/') for n in names)

    # повтор без force в тот же день — None
    assert backup.create_backup(force=False) is None

    # ротация daily
    for i in range(5):
        backup.create_backup(force=True, kind='daily')
    dailies = [p for p in backup.list_backups() if p.name.startswith('daily_')]
    assert len(dailies) <= 3

    # restore
    (data / 'настройки.yaml').write_text('broken: true\n', encoding='utf-8')
    report = backup.restore_backup(a1)
    assert 'настройки.yaml' in report['restored']
    assert 'broken' not in (data / 'настройки.yaml').read_text(encoding='utf-8')


def test_bad_archive_message(tmp_path):
    bad = tmp_path / 'bad.zip'
    bad.write_bytes(b'not-a-zip')
    report = backup.restore_backup(bad)
    assert report['errors']
    assert 'поврежд' in report['errors'][0].lower() or 'zip' in report['errors'][0].lower()
