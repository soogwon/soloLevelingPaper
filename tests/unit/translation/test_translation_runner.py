"""실제 .env나 API를 사용하지 않고 번역 실행기의 안전 장치를 검증한다."""

from unittest.mock import Mock

import pytest
import requests

from scripts.check_translation import main


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    for key in ('OPENAI_API_KEY', 'TRANSLATION_PROVIDER', 'TRANSLATION_MODEL', 'TRANSLATION_PROMPT_VERSION',
                'TRANSLATION_TARGET_LANGUAGE', 'TRANSLATION_TIMEOUT_SECONDS', 'TRANSLATION_MAX_OUTPUT_TOKENS',
                'ALLOW_EXTERNAL_API', 'LOCAL_ONLY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('외부 호출 금지')))
    path = tmp_path / '.env'
    path.write_text('OPENAI_API_KEY=test-secret\nALLOW_EXTERNAL_API=true\n', encoding='utf-8-sig')
    return path


def test_default_run_does_not_construct_provider(env_file, monkeypatch, capsys):
    provider = Mock(side_effect=AssertionError('기본 실행은 제공자를 구성하지 않음'))
    monkeypatch.setattr('scripts.check_translation.OpenAITranslationProvider', provider)
    assert main(['--env-file', str(env_file)]) == 0
    output = capsys.readouterr()
    assert 'test-secret' not in output.out + output.err
    provider.assert_not_called()


def test_process_environment_can_block_live(env_file, monkeypatch):
    monkeypatch.setenv('LOCAL_ONLY', 'true')
    assert main(['--env-file', str(env_file), '--live']) == 2


def test_live_with_fake_translation(env_file, monkeypatch, capsys):
    provider = Mock()
    provider.translate.return_value = '합성 데이터 100개에서 정확도는 82%였다. 다른 조건은 평가하지 않았다.'
    monkeypatch.setattr('scripts.check_translation.OpenAITranslationProvider', Mock(return_value=provider))
    assert main(['--env-file', str(env_file), '--live']) == 0
    provider.translate.assert_called_once()
    assert 'test-secret' not in capsys.readouterr().out
