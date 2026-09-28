"""합성 설정 파일로 로더와 실행 안전 장치를 확인한다. 실제 키·API를 사용하지 않는다."""

from unittest.mock import Mock

import pytest
import requests

from scripts.check_generation import load_settings, main
from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('실제 HTTP 호출 금지')))
    for key in ('GENERATION_PROVIDER', 'GENERATION_MODEL', 'OPENAI_API_KEY',
                'GENERATION_TIMEOUT_SECONDS', 'GENERATION_MAX_OUTPUT_TOKENS',
                'ALLOW_EXTERNAL_API', 'LOCAL_ONLY'):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / '.env'
    path.write_text('OPENAI_API_KEY="test-secret"\nALLOW_EXTERNAL_API=true\nLOCAL_ONLY=false\n', encoding='utf-8-sig')
    return path


def test_file_settings_and_environment_priority(env_file, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'existing-secret')
    monkeypatch.setenv('ALLOW_EXTERNAL_API', 'false')
    settings = load_settings(env_file)
    assert settings.api_key == 'existing-secret'
    assert not settings.allow_external_api
    import os
    assert os.environ['OPENAI_API_KEY'] == 'existing-secret'
    assert 'LOCAL_ONLY' not in os.environ


def test_no_interpolation(env_file):
    env_file.write_text('OPENAI_API_KEY=${OTHER}\n', encoding='utf-8')
    assert load_settings(env_file, {'OTHER': 'hidden'}).api_key == '${OTHER}'


def test_dry_run_never_constructs_generator(env_file, monkeypatch, capsys):
    generator = Mock(side_effect=AssertionError('기본 실행은 생성기를 만들지 않음'))
    monkeypatch.setattr('scripts.check_generation.OpenAIClaimGenerator', generator)
    assert main(['--env-file', str(env_file)]) == 0
    output = capsys.readouterr()
    assert '실제 API 호출 없음' in output.out
    assert 'test-secret' not in output.out + output.err
    generator.assert_not_called()


def test_live_requires_external_permission(env_file, monkeypatch, capsys):
    monkeypatch.setenv('LOCAL_ONLY', 'true')
    assert main(['--env-file', str(env_file), '--live']) == 2
    assert '호출 차단' in capsys.readouterr().err


@pytest.mark.parametrize('valid', [True, False])
def test_live_flow_with_fake_generator(env_file, monkeypatch, valid, capsys):
    draft = GeneratedAnswerDraft((GeneratedClaim('합성 주장', ('sample-ev-1' if valid else 'unknown',)),))
    instance = Mock()
    instance.generate_claims.return_value = draft
    monkeypatch.setattr('scripts.check_generation.OpenAIClaimGenerator', Mock(return_value=instance))
    assert main(['--env-file', str(env_file), '--live']) == (0 if valid else 1)
    instance.generate_claims.assert_called_once()
    output = capsys.readouterr()
    assert 'test-secret' not in output.out + output.err


def test_missing_file_is_sanitized(tmp_path, capsys):
    assert main(['--env-file', str(tmp_path / 'secret-missing')]) == 2
    assert 'secret-missing' not in capsys.readouterr().err


def test_invalid_value_does_not_leak(env_file, capsys):
    env_file.write_text('OPENAI_API_KEY=test-secret\nGENERATION_MAX_OUTPUT_TOKENS=private-value\n', encoding='utf-8')
    assert main(['--env-file', str(env_file)]) == 2
    output = capsys.readouterr()
    assert 'private-value' not in output.err and 'test-secret' not in output.err


def test_script_supports_non_korean_console(env_file):
    import os
    from pathlib import Path
    import subprocess
    import sys
    script = Path(__file__).resolve().parents[2] / 'scripts/check_generation.py'
    result = subprocess.run([sys.executable, str(script), '--env-file', str(env_file)],
        env={**os.environ, 'PYTHONIOENCODING': 'cp1252'}, capture_output=True,
        encoding='utf-8', timeout=10)
    assert result.returncode == 0
    assert '실제 API 호출 없음' in result.stdout
    assert 'test-secret' not in result.stdout + result.stderr
