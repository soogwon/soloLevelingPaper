"""외부 호출과 실제 대기 없이 번역 재시도·안전 분류를 검증한다."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import pytest

from solo_leveling.application.translation.ports import ProviderFailure, TranslationProviderUnavailable
from solo_leveling.application.translation.retry import TranslationRetryPolicy
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import Chunk
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.translation.openai_provider import _http_failure, _retry_after


SETTINGS = TranslationSettings('fake', 'test', 'test')
CHUNKS = [Chunk('secret-id', 'parse', 0, 'secret-source'), Chunk('c2', 'parse', 1, 'other')]


def failure(code=ProviderFailure.TIMEOUT, delay=None):
    return TranslationProviderUnavailable('secret-key', code=code, retry_after_seconds=delay)


def service(provider, **kwargs):
    return TranslationService(provider, sleep=kwargs.pop('sleep', Mock()),
                              random_value=lambda: 0, **kwargs)


def test_only_failed_chunk_retried_and_diagnostics_safe(capsys):
    provider = Mock()
    provider.translate.side_effect = ['성공', failure(), '복구']
    wait = Mock()
    batch = service(provider, sleep=wait).translate(CHUNKS, SETTINGS)
    assert batch.success_count == 2
    assert [call.args[0].chunk_id for call in provider.translate.call_args_list] == ['secret-id', 'c2', 'c2']
    wait.assert_called_once_with(1.0)
    output = capsys.readouterr()
    assert output.out == '' and '"code": "timeout"' in output.err
    assert all(secret not in output.err for secret in ('secret-key', 'secret-id', 'secret-source'))


def test_attempt_cap_and_legacy_storage_code():
    provider = Mock()
    provider.translate.side_effect = failure()
    wait = Mock()
    batch = service(provider, sleep=wait).translate(CHUNKS[:1], SETTINGS)
    assert provider.translate.call_count == 3
    assert [c.args[0] for c in wait.call_args_list] == [1., 2.]
    assert batch.items[0].failure_code.value == 'provider_unavailable'


@pytest.mark.parametrize('code', [c for c in ProviderFailure if c not in (
    ProviderFailure.TIMEOUT, ProviderFailure.CONNECTION, ProviderFailure.RATE_LIMIT, ProviderFailure.SERVER)])
def test_non_retryable(code):
    provider = Mock()
    provider.translate.side_effect = failure(code)
    wait = Mock()
    service(provider, sleep=wait).translate(CHUNKS[:1], SETTINGS)
    provider.translate.assert_called_once()
    wait.assert_not_called()


@pytest.mark.parametrize('delay,calls', [(5., 3), (11., 1)])
def test_server_delay_not_shortened(delay, calls):
    provider = Mock()
    provider.translate.side_effect = failure(ProviderFailure.RATE_LIMIT, delay)
    wait = Mock()
    service(provider, sleep=wait).translate(CHUNKS[:1], SETTINGS)
    assert provider.translate.call_count == calls
    assert all(c.args[0] >= delay for c in wait.call_args_list)


@pytest.mark.parametrize('policy', [TranslationRetryPolicy(max_batch_retries=1),
    TranslationRetryPolicy(max_batch_wait_seconds=1)])
def test_batch_budget_shared_between_chunks(policy):
    provider = Mock()
    provider.translate.side_effect = failure()
    batch = service(provider, retry_policy=policy).translate(CHUNKS, SETTINGS)
    assert batch.failure_count == 2 and provider.translate.call_count == 3


@pytest.mark.parametrize('window', ['chunk_retry_window_seconds', 'batch_retry_window_seconds'])
def test_elapsed_window_blocks_retry(window):
    now = [0.]
    provider = Mock()
    def fail(*args):
        now[0] += 2
        raise failure()
    provider.translate.side_effect = fail
    wait = Mock()
    policy = replace(TranslationRetryPolicy(), **{window: 2.})
    service(provider, clock=lambda: now[0], sleep=wait, retry_policy=policy).translate(CHUNKS[:1], SETTINGS)
    provider.translate.assert_called_once()
    wait.assert_not_called()


def test_oversleep_does_not_start_late_retry():
    now = [0.]
    provider = Mock()
    provider.translate.side_effect = failure()
    def sleep(_):
        now[0] += 100
    service(provider, sleep=sleep, clock=lambda: now[0]).translate(CHUNKS[:1], SETTINGS)
    provider.translate.assert_called_once()


def test_reused_revision_preserves_policy_and_dependencies():
    original = service(Mock(), retry_policy=TranslationRetryPolicy(max_attempts=1))
    reused = original.with_revision_id('existing')
    assert reused.retry_policy is original.retry_policy
    assert reused.sleep is original.sleep and reused.clock is original.clock
    original.provider.translate.side_effect = failure()
    result = reused.translate(CHUNKS[:1], SETTINGS)
    assert result.revision.translation_revision_id == 'existing'
    original.provider.translate.assert_called_once()


@pytest.mark.parametrize('status,body,code', [
    (401, {}, ProviderFailure.AUTHENTICATION), (403, {}, ProviderFailure.PERMISSION),
    (400, {}, ProviderFailure.INVALID_REQUEST), (503, {}, ProviderFailure.SERVER),
    (429, {'error': {'code': 'rate_limit_exceeded'}}, ProviderFailure.RATE_LIMIT),
    (429, {'error': {'code': 'slow_down'}}, ProviderFailure.RATE_LIMIT),
    (429, {'error': {'code': 'insufficient_quota'}}, ProviderFailure.QUOTA),
    (429, {'error': {'type': 'insufficient_quota', 'code': 'slow_down'}}, ProviderFailure.QUOTA),
    (429, {'error': {'code': 'credit_balance_exhausted'}}, ProviderFailure.QUOTA),
    (429, {'error': {'code': 'unknown-secret'}}, ProviderFailure.UNKNOWN),
    (429, None, ProviderFailure.UNKNOWN), (302, {}, ProviderFailure.UNKNOWN),
])
def test_http_classification(status, body, code):
    response = Mock(status_code=status, headers={'Retry-After': '2'})
    response.json.return_value = body
    result = _http_failure(response)
    assert result.code == code and result.retry_after_seconds == 2
    assert 'secret' not in str(result)


@pytest.mark.parametrize('value', ['NaN', 'inf', '-1', 'secret', '', None, 'x' * 200])
def test_invalid_retry_after(value):
    assert _retry_after(value) is None


@pytest.mark.parametrize('kwargs', [{'max_attempts': True}, {'max_attempts': 4},
    {'max_batch_retries': -1}, {'max_delay_seconds': float('nan')}, {'jitter_seconds': -1}])
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        TranslationRetryPolicy(**kwargs)


def test_98_chunks_only_two_transient_failures_retried():
    calls = {}
    def translate(request, settings):
        count = calls.get(request.chunk_id, 0) + 1
        calls[request.chunk_id] = count
        if request.chunk_id in ('2', '8') and count == 1:
            raise failure(ProviderFailure.SERVER)
        return '번역'
    provider = Mock()
    provider.translate.side_effect = translate
    chunks = [Chunk(str(i), 'p', i, 'source') for i in range(98)]
    result = service(provider).translate(chunks, SETTINGS)
    assert result.success_count == 98 and provider.translate.call_count == 100
    assert {k for k, v in calls.items() if v == 2} == {'2', '8'}


def test_jitter_and_batch_budget_reset_per_call():
    provider = Mock()
    provider.translate.side_effect = [failure(), '성공', failure(), '성공']
    wait = Mock()
    instance = TranslationService(provider, sleep=wait, random_value=lambda: 1,
        retry_policy=TranslationRetryPolicy(max_batch_retries=1))
    for _ in range(2):
        assert instance.translate(CHUNKS[:1], SETTINGS).success_count == 1
    assert [c.args[0] for c in wait.call_args_list] == [1.25, 1.25]


def test_http_date_retry_after():
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    assert 28 <= _retry_after(future) <= 30
    assert _retry_after('Wed, 01 Jan 2020 00:00:00 GMT') == 0


def test_unknown_429_non_json_does_not_retry():
    response = Mock(status_code=429, headers={})
    response.json.side_effect = ValueError('secret')
    error = _http_failure(response)
    assert error.code == ProviderFailure.UNKNOWN and not error.retryable


def test_broken_stderr_does_not_change_failure_result(monkeypatch):
    provider = Mock()
    provider.translate.side_effect = failure(ProviderFailure.QUOTA)
    stderr = Mock()
    stderr.write.side_effect = OSError('closed')
    monkeypatch.setattr('sys.stderr', stderr)
    assert service(provider).translate(CHUNKS[:1], SETTINGS).failure_count == 1
