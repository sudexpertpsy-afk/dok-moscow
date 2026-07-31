"""Тесты модуля paths: каталоги данных и миграция legacy."""

from pathlib import Path

from docfiller_core import paths


def test_data_and_output_dirs_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    # Path.home() читает HOME
    assert paths.data_dir() == tmp_path / 'Library' / 'Application Support' / 'Шаблонер'
    assert paths.output_dir() == (
        tmp_path / 'Documents' / 'Шаблонер' / 'Готовые_документы'
    )


def test_ensure_dirs_creates_structure(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    paths.ensure_dirs()
    assert paths.data_dir().is_dir()
    assert paths.output_dir().is_dir()
    assert paths.backups_dir().is_dir()


def test_migrate_legacy_copies_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    app_root = tmp_path / 'app'
    app_root.mkdir()
    (app_root / 'настройки.yaml').write_text('организация: {}\n', encoding='utf-8')
    (app_root / '.counters.json').write_text('{}', encoding='utf-8')
    (app_root / '.journal.jsonl').write_text(
        '{"ts":"1","template":"T","output":"o","context":{}}\n',
        encoding='utf-8',
    )
    (app_root / 'Реестр_договоров.xlsx').write_bytes(b'PK\x03\x04fake')
    out_legacy = app_root / 'Готовые_документы' / '2026-01-01'
    out_legacy.mkdir(parents=True)
    (out_legacy / 'doc.docx').write_text('x', encoding='utf-8')

    report = paths.migrate_legacy_data(app_root=app_root)
    assert paths.config_path().exists()
    assert paths.counters_path().exists()
    assert paths.journal_path().exists()
    assert (paths.registries_dir() / 'Реестр_договоров.xlsx').exists()
    assert (paths.output_dir() / '2026-01-01' / 'doc.docx').exists()
    assert report['backup_dir']
    assert Path(report['backup_dir']).is_dir()
    assert len(report['migrated']) >= 4

    # Повторная миграция не затирает и не дублирует работу
    paths.config_path().write_text('организация:\n  инн: "1"\n', encoding='utf-8')
    report2 = paths.migrate_legacy_data(app_root=app_root)
    assert 'инн' in paths.config_path().read_text(encoding='utf-8')
    assert 'настройки.yaml' in report2['skipped'] or not report2['migrated']


def test_find_named_nfc(tmp_path):
    target = tmp_path / 'настройки.yaml'
    target.write_text('x', encoding='utf-8')
    found = paths.find_named(tmp_path, 'настройки.yaml')
    assert found is not None
    assert found.exists()
