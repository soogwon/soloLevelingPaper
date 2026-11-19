"""품질 실패만 한 번 복구하고 원문 폴백을 번역 성공과 구분한다."""
from unittest.mock import Mock

import pytest

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.translation.retry import TranslationRetryPolicy
from solo_leveling.application.translation.ports import TranslationProviderUnavailable
from solo_leveling.domain.models import Chunk
from solo_leveling.domain.translation import TranslationSettings, TranslationStatus, TranslationFailureCode

SOURCE = 'This synthetic source preserves the original text.'
SETTINGS = TranslationSettings('fake','synthetic','v1')


def run(responses, enabled=True):
    provider = Mock()
    provider.translate.side_effect = responses
    service = TranslationService(provider,retry_policy=TranslationRetryPolicy(max_attempts=1),quality_retry_once=enabled)
    return service.translate((Chunk('c','p',0,SOURCE),),SETTINGS), provider


def test_quality_retry_once_changes_instruction_policy_but_not_original(capsys):
    result, provider = run([SOURCE,'합성 원문을 보존합니다.'])
    assert provider.translate.call_count == 2
    first, second = [call.args[0] for call in provider.translate.call_args_list]
    assert first.original_text == second.original_text == SOURCE
    assert first.recovery_policy is None and second.recovery_policy == 'layout-aware-v1'
    assert result.success_count == 1
    assert SOURCE not in capsys.readouterr().err


def test_final_quality_failure_keeps_original_and_failure_not_success():
    result, provider = run([SOURCE,'Still only English prose.'])
    item = result.items[0]
    assert item.text == SOURCE and item.status == TranslationStatus.ORIGINAL_ONLY
    assert item.failure_code == TranslationFailureCode.TARGET_LANGUAGE_MISMATCH
    assert item.indexable and not item.succeeded
    assert result.failure_count == 1 and result.success_count == 0
    assert provider.translate.call_count == 2


def test_provider_failure_is_not_convertible_to_original_only():
    result, provider = run([SOURCE,TranslationProviderUnavailable()])
    assert not result.items[0].indexable
    assert result.items[0].status == TranslationStatus.TRANSLATION_FAILED
    assert provider.translate.call_count == 2


def test_default_policy_does_not_add_external_call():
    result, provider = run([SOURCE],enabled=False)
    assert provider.translate.call_count == 1 and not result.items[0].indexable


def test_quality_recovery_rejects_nested_provider_retries():
    with pytest.raises(ValueError): TranslationService(Mock(),quality_retry_once=True)


def test_reference_is_not_retried_or_sent():
    provider = Mock()
    result = TranslationService(provider,retry_policy=TranslationRetryPolicy(max_attempts=1),quality_retry_once=True).translate(
        (Chunk('c','p',0,'https://doi.org/10.5555/12345'),),SETTINGS)
    provider.translate.assert_not_called()
    assert result.items[0].status == TranslationStatus.REFERENCE_CONTENT


def test_expired_recovery_window_does_not_add_call():
    now = [0.]
    provider = Mock()
    def respond(*args):
        now[0] = 400.
        return SOURCE
    provider.translate.side_effect = respond
    result = TranslationService(provider,retry_policy=TranslationRetryPolicy(max_attempts=1),
        quality_retry_once=True,clock=lambda:now[0]).translate((Chunk('c','p',0,SOURCE),),SETTINGS)
    assert provider.translate.call_count == 1
    assert result.items[0].status == TranslationStatus.ORIGINAL_ONLY
