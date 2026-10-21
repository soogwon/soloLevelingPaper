"""Desktop 설정이 기존 데이터와 키를 건드리지 않는지 검사한다."""

import json
import sys

import pytest

from solo_leveling.interfaces.cli import desktop


def config_file(tmp_path, monkeypatch, extra=''):
    for key in ('DATA_ROOT', 'SOLO_LEVELING_ENV_FILE', 'HF_HOME', 'HF_HUB_CACHE',
                'OPENAI_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / '한글 설정.env'
    path.write_text(f'DATA_ROOT={tmp_path.as_posix()}/data\n'
                    'OPENAI_API_KEY=private-test-secret\n' + extra, encoding='utf-8')
    return path


def test_config_is_only_json_without_secret_or_data_creation(tmp_path, monkeypatch, capsys):
    path = config_file(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert desktop.main(['config', '--env-file', str(path)]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)['mcpServers']['solo-leveling-paper']
    assert result['command'] == sys.executable
    assert result['args'][-1] == str(path)
    assert 'private-test-secret' not in output.out
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('value', ['', 'relative/data', '../data'])
def test_relative_data_rejected_without_secret(tmp_path, monkeypatch, capsys, value):
    path = config_file(tmp_path, monkeypatch, f'DATA_ROOT={value}\n')
    assert desktop.main(['config', '--env-file', str(path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert 'DESKTOP_SETUP_FAILED' in captured.err
    assert 'private-test-secret' not in captured.err


def test_missing_file_rejected(tmp_path, capsys):
    assert desktop.main(['serve', '--env-file', str(tmp_path / 'missing')]) == 1
    assert not capsys.readouterr().out


def test_serve_uses_explicit_log_and_cache_without_preloading(tmp_path, monkeypatch):
    from solo_leveling.interfaces.cli import mcp_diagnostics
    path = config_file(tmp_path, monkeypatch, f'HF_HOME={tmp_path.as_posix()}/models\n')
    calls = []
    monkeypatch.setattr(mcp_diagnostics, 'main', lambda args: calls.append(args) or 0)
    assert desktop.main(['serve', '--env-file', str(path)]) == 0
    assert calls == [['--log-dir', str(tmp_path / 'data' / 'diagnostics')]]
    assert desktop.os.environ['HF_HOME'] == f'{tmp_path.as_posix()}/models'


def test_prepare_uses_same_cache_without_api_key(tmp_path, monkeypatch):
    from solo_leveling.interfaces.cli import prepare_embedding_model
    path = config_file(tmp_path, monkeypatch,
                       f'OPENAI_API_KEY=\nHF_HOME={tmp_path.as_posix()}/models\n')
    calls = []
    monkeypatch.setattr(prepare_embedding_model, 'main', lambda args: calls.append(args) or 0)
    assert desktop.main(['prepare-model', '--env-file', str(path), '--offline']) == 0
    assert calls == [['--offline']]


def test_process_override_is_preserved(tmp_path, monkeypatch):
    path = config_file(tmp_path, monkeypatch)
    previous = tmp_path / 'existing-data'
    monkeypatch.setenv('DATA_ROOT', str(previous))
    _, paths = desktop.checked_environment(path)
    assert paths.data_root == previous
    assert not previous.exists()


@pytest.mark.parametrize(('key', 'value'), [
    ('EMBEDDING_PROCESS_TIMEOUT_SECONDS', 'invalid'),
    ('EMBEDDING_PROCESS_TIMEOUT_SECONDS', 'nan'),
    ('EMBEDDING_PROCESS_TIMEOUT_SECONDS', 'inf'),
    ('EMBEDDING_PROCESS_TIMEOUT_SECONDS', '0'),
    ('EMBEDDING_PROCESS_TIMEOUT_SECONDS', '-1'),
    ('MAX_UPLOAD_BYTES', 'invalid'),
    ('MAX_UPLOAD_BYTES', '0'),
    ('MAX_UPLOAD_BYTES', '-1'),
    ('MAX_CONCURRENT_INGESTIONS', 'invalid'),
    ('MAX_CONCURRENT_INGESTIONS', '0'),
    ('MAX_CONCURRENT_INGESTIONS', '2'),
    ('OPENAI_API_KEY', ''),
    ('TRANSLATION_TIMEOUT_SECONDS', 'invalid'),
])
def test_check_and_start_reject_same_settings_before_db(
        tmp_path, monkeypatch, capsys, key, value):
    from unittest.mock import Mock
    from solo_leveling.interfaces.mcp import runtime

    path = config_file(tmp_path, monkeypatch, f'{key}={value}\n')
    monkeypatch.delenv(key, raising=False)
    database = Mock(side_effect=AssertionError('DB 접근 금지'))
    interrupted = Mock(side_effect=AssertionError('기존 작업 변경 금지'))
    embedder = Mock(side_effect=AssertionError('임베더 생성 금지'))
    monkeypatch.setattr(runtime, 'init_db', database)
    monkeypatch.setattr(runtime, 'mark_interrupted_jobs_on_startup', interrupted)
    monkeypatch.setattr(runtime, 'ProcessEmbedder', embedder)
    assert desktop.main(['check', '--env-file', str(path)]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert 'private-test-secret' not in output.err
    assert 'DESKTOP_SETUP_FAILED' in output.err
    with pytest.raises(ValueError):
        runtime.build_runtime_server(runtime.load_environment(path))
    database.assert_not_called()
    interrupted.assert_not_called()
    embedder.assert_not_called()
    assert not (tmp_path / 'data').exists()
