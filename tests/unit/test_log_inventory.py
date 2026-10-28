"""보관 검토가 본문과 사용자 파일을 건드리지 않는지 검증한다."""

import json
import os
from pathlib import Path

import pytest

from solo_leveling.interfaces.cli.log_inventory import inventory, main


NAME = 'worker-' + 'a' * 32 + '.log'


def test_preview_reports_limits_without_reading_or_changing_files(tmp_path, monkeypatch):
    log = tmp_path / NAME
    log.write_bytes(b'private content')
    os.utime(log, (100, 100))
    (tmp_path / 'user.pdf').write_bytes(b'paper')
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / NAME).write_bytes(b'nested')
    before = log.stat()
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('본문 읽기 금지'))
    result = inventory(tmp_path, now=100 + 31 * 86400, max_bytes=1)
    assert result['file_count'] == 1
    assert result['bytes_exceeded']
    assert result['skipped_count'] == 2
    assert result['files'][0]['age_review']
    assert result['files'][0]['kind'] == 'unknown'
    assert result['files'][0]['activity'] == 'unknown'
    assert log.stat().st_mtime_ns == before.st_mtime_ns
    assert log.stat().st_size == before.st_size


def test_empty_directory_and_missing_directory(tmp_path, capsys):
    assert inventory(tmp_path)['file_count'] == 0
    missing = tmp_path / 'missing'
    assert main(['--log-dir', str(missing)]) == 2
    assert json.loads(capsys.readouterr().out) == {'error_code': 'LOG_INVENTORY_UNAVAILABLE'}
    assert not missing.exists()


def test_stack_classification_and_exact_limits(tmp_path):
    (tmp_path / ('worker-stack-' + 'b' * 32 + '.log')).write_bytes(b'abc')
    result = inventory(tmp_path, max_bytes=3, max_files=1)
    assert result['files'][0]['kind'] == 'raw_stack'
    assert not result['bytes_exceeded']
    assert not result['count_exceeded']


def test_hard_links_are_excluded(tmp_path):
    original = tmp_path / 'original'
    original.write_bytes(b'data')
    os.link(original, tmp_path / NAME)
    assert inventory(tmp_path)['file_count'] == 0


def test_symbolic_links_are_excluded(tmp_path):
    original = tmp_path / 'original'
    original.write_bytes(b'data')
    try:
        (tmp_path / NAME).symlink_to(original)
    except OSError:
        pytest.skip('현재 환경에서 심볼릭 링크 생성 권한 없음')
    assert inventory(tmp_path)['file_count'] == 0
    folder = tmp_path / 'alias'
    folder.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        inventory(folder)


def test_file_disappearing_during_scan_is_reported(tmp_path, monkeypatch):
    (tmp_path / NAME).write_bytes(b'data')
    original = Path.lstat
    def disappearing(path, *args, **kwargs):
        if path.name == NAME:
            raise PermissionError('비공개 경로 및 오류')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', disappearing)
    result = inventory(tmp_path)
    assert not result['complete']
    assert result['unreadable_count'] == 1
    assert '비공개' not in json.dumps(result)


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), True])
def test_invalid_age_policy(tmp_path, value):
    with pytest.raises(ValueError):
        inventory(tmp_path, review_days=value)
