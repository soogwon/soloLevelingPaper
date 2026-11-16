"""A 공개 계약에 대한 B 분류 힌트. HTTP 응답·영속 상태 검증이라고 주장하지 않는다."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
import json

import pytest
import requests

from solo_leveling.application.processing_outcomes import (
    processing_error_advice, processing_phase_hint, processing_result_advice,
)
from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded, DispatchError
from solo_leveling.domain.remote_processing import ProcessingError, ProcessingResult
from solo_leveling.domain.translation import TranslationFailureCode
from tests.unit.test_remote_processing_stages import (
    envelope,
    forbid_network_and_models as forbid_network_and_models,
    setup as setup,
)


@pytest.fixture(name='processing_setup')
def shared_processing_setup(request):
    # pytest에 공유 fixture를 명시적으로 노출하고 테스트 인자와 import 이름은 분리한다.
    return request.getfixturevalue('setup')


SOURCE_CODES = [
    'PROCESSING_PDF_MISMATCH', 'PROCESSING_PDF_UNREADABLE', 'PROCESSING_PDF_ENCRYPTED',
    'PROCESSING_EMPTY_DOCUMENT', 'PROCESSING_LIMIT_EXCEEDED',
]
INTERNAL_CODES = [
    'PROCESSING_CONFIG_INVALID', 'PROCESSING_INPUT_INVALID', 'PROCESSING_PARSE_INVALID',
    'PROCESSING_PARSE_FAILED', 'PROCESSING_TRANSLATION_GATE_REQUIRED',
    'PROCESSING_TRANSLATION_CONFIG_MISMATCH', 'PROCESSING_TRANSLATION_FAILED',
    'PROCESSING_TRANSLATION_INVALID', 'PROCESSING_EMBEDDING_FAILED', 'PROCESSING_EMBEDDING_INVALID',
    'PROCESSING_NOT_ALLOWED', 'PROCESSING_CHECK_UNAVAILABLE',
]
DISPATCH_CODES = [
    'DISPATCH_INTENT_INVALID', 'DISPATCH_REQUEST_INVALID', 'DISPATCH_BLOCKED',
    'DISPATCH_NOT_AUTHORIZED', 'DISPATCH_AUTHORIZATION_UNAVAILABLE', 'DISPATCH_PERMIT_INVALID',
    'DISPATCH_RESERVATION_EXCEEDED', 'DISPATCH_DISABLED', 'DISPATCH_PAYLOAD_INVALID',
    'DISPATCH_PREPARATION_UNAVAILABLE', 'DISPATCH_PROVIDER_UNCERTAIN',
]


def assert_hint(hint, action, code, retryable):
    assert asdict(hint) == dict(action=action, error_code=code, retryable=retryable,
                              requires_authoritative_review=True)
    assert hint.retryable is not True
    assert not {'status', 'phase', 'job_id', 'published_revision_id', 'searchable'} & asdict(hint).keys()


@pytest.mark.parametrize('code', SOURCE_CODES)
def test_known_input_failure_does_not_authorize_same_input_retry(code):
    assert_hint(processing_error_advice(ProcessingError(code)), 'stop', 'source_validation_failed', False)


@pytest.mark.parametrize('code', INTERNAL_CODES)
def test_internal_failure_and_checkpoint_denial_do_not_invent_public_reason(code):
    assert_hint(processing_error_advice(ProcessingError(code)), 'stop', 'processing_failed', None)


@pytest.mark.parametrize('code', DISPATCH_CODES)
def test_dispatch_denial_and_unknown_block_reason_require_actual_ledger_review(code):
    assert_hint(processing_error_advice(DispatchError(code)), 'stop', 'processing_failed', None)


@pytest.mark.parametrize('reason', ['time', 'tokens'])
def test_local_execution_limit_is_not_assumed_to_be_monetary_reservation_failure(reason):
    assert_hint(processing_error_advice(DispatchBudgetExceeded(reason)), 'stop', 'processing_failed', None)


@pytest.mark.parametrize('code', ['DISPATCH_USAGE_UNKNOWN', 'DISPATCH_RECONCILIATION_UNAVAILABLE'])
def test_usage_uncertainty_is_pending_and_never_an_ordinary_provider_retry(code):
    assert_hint(processing_error_advice(DispatchError(code)), 'reconcile_usage', 'usage_pending', None)


@pytest.mark.parametrize('error', [
    ValueError('DISPATCH_USAGE_UNKNOWN'), ProcessingError('DISPATCH_USAGE_UNKNOWN'),
    DispatchError('PROCESSING_PDF_MISMATCH'), ProcessingError('processing_cancelled'),
    ProcessingError('lease_expired'), ProcessingError('ai_consent_required'),
    ProcessingError('provider_temporarily_unavailable'), ProcessingError('NEW_INTERNAL_CODE'),
    ProcessingError('PROCESSING_PDF_MISMATCH\nprivate-body'),
    ProcessingError('PROCESSING_PDF_MISMATCH', 'private-body'),
    ProcessingError(), ProcessingError(['PROCESSING_PDF_MISMATCH']),
    {'error_code': 'DISPATCH_USAGE_UNKNOWN'}, None,
])
def test_only_exact_internal_type_and_single_fixed_code_are_trusted(error):
    assert_hint(processing_error_advice(error), 'stop', 'processing_failed', None)


def test_exception_string_repr_and_cause_are_not_evaluated_or_disclosed(capsys):
    class PrivateError(RuntimeError):
        def __str__(self):
            raise AssertionError('must not stringify')

        def __repr__(self):
            raise AssertionError('must not repr')

    private = PrivateError('secret-key', 'private-source-text')
    error = ProcessingError('PROCESSING_TRANSLATION_FAILED')
    error.__cause__ = private
    for candidate in (private, error, ProcessingError(private)):
        serialized = json.dumps(asdict(processing_error_advice(candidate)))
        assert 'secret' not in serialized and 'private' not in serialized
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('stage,expected', [
    ('parse', 'parsing'), ('translation', 'translating'), ('embedding', 'embedding'),
    ('result', None), ('publishing', None), ('finished', None), ('prepared', None),
    ('private-phase', None), (None, None), ([], None),
])
def test_checkpoint_hint_never_invents_publishing_terminal_or_fallback_phase(stage, expected):
    assert processing_phase_hint(stage) == expected


def test_prepared_is_only_a_publication_candidate_and_classification_has_no_side_effects(processing_setup):
    stages, source, pdf, ledger, post, *_ = processing_setup
    before = deepcopy(source), pdf[:]
    result = stages.run(source, pdf)
    snapshot = deepcopy(result)
    counts = len(ledger.requests), len(ledger.observed), len(ledger.unknowns), post.call_count
    for _ in range(2):
        advice = processing_result_advice(result)
        assert_hint(advice, 'await_publication', None, None)
        with pytest.raises(FrozenInstanceError):
            advice.retryable = True
    assert result == snapshot and (source, pdf) == before
    assert (len(ledger.requests), len(ledger.observed), len(ledger.unknowns), post.call_count) == counts


def test_real_stages_partial_translation_is_unpublished_failure_not_temporary_provider(processing_setup):
    stages, source, pdf, ledger, post, response, _ = processing_setup
    response.json.return_value = envelope('')
    result = stages.run(source, pdf)
    assert result.status == 'translation_incomplete' and result.embeddings is None
    assert all(item.failure_code == TranslationFailureCode.EMPTY_TRANSLATION for item in result.translations)
    assert_hint(processing_result_advice(result), 'stop', 'processing_failed', None)
    stages.embedder.embed.assert_not_called()
    assert post.call_count == len(ledger.observed) == 2


@pytest.mark.parametrize('failure', list(TranslationFailureCode))
def test_chunk_failure_lacks_evidence_for_temporary_outage_or_safe_resubmission(processing_setup, failure):
    stages, source, pdf, *_ = processing_setup
    prepared = stages.run(source, pdf)
    failed = replace(prepared.translations[0], text=None, failure_code=failure)
    partial = replace(prepared, translations=(failed, *prepared.translations[1:]),
                      embeddings=None, status='translation_incomplete')
    before = deepcopy(partial)
    assert_hint(processing_result_advice(partial), 'stop', 'processing_failed', None)
    assert partial == before


@pytest.mark.parametrize('status', ['succeeded', 'cancelled', 'publishing', 'private-status', [], None])
def test_unknown_internal_status_is_not_promoted_or_echoed(processing_setup, status):
    stages, source, pdf, *_ = processing_setup
    result = replace(stages.run(source, pdf), status=status)
    assert_hint(processing_result_advice(result), 'stop', 'processing_failed', None)


def test_non_result_payload_cannot_claim_prepared():
    assert_hint(processing_result_advice({'status': 'prepared'}), 'stop', 'processing_failed', None)


def test_status_without_data_cannot_become_publication_candidate():
    malformed = ProcessingResult(None, None, None, (), None, (), 'prepared')
    assert_hint(processing_result_advice(malformed), 'stop', 'processing_failed', None)


@pytest.mark.parametrize('mutation', [
    'source', 'document', 'settings', 'translations', 'failed-translation', 'empty-text',
    'wrong-translation-id', 'no-embedding', 'wrong-embedding-ids', 'no-vectors',
    'empty-vector', 'wrong-vector-size', 'wrong-parse-revision', 'duplicate-id',
])
def test_incomplete_or_misconnected_prepared_is_rejected(processing_setup, mutation):
    stages, source, pdf, *_ = processing_setup
    result = stages.run(source, pdf)
    if mutation == 'source': result = replace(result, source=None)
    elif mutation == 'document': result = replace(result, document=None)
    elif mutation == 'settings': result = replace(result, translation_settings=None)
    elif mutation == 'translations': result = replace(result, translations=())
    elif mutation in ('failed-translation', 'empty-text', 'wrong-translation-id'):
        changes = {'failed-translation': dict(text=None, failure_code=TranslationFailureCode.PROVIDER_UNAVAILABLE),
                   'empty-text': dict(text=' '), 'wrong-translation-id': dict(chunk_id='other-chunk')}[mutation]
        result = replace(result, translations=(replace(result.translations[0], **changes), *result.translations[1:]))
    elif mutation == 'no-embedding': result = replace(result, embeddings=None)
    elif mutation == 'wrong-embedding-ids':
        result = replace(result, embeddings=replace(result.embeddings, chunk_ids=('other-chunk', 'another-chunk')))
    elif mutation == 'no-vectors': result = replace(result, embeddings=replace(result.embeddings, vectors=()))
    elif mutation in ('empty-vector', 'wrong-vector-size'):
        vector = () if mutation == 'empty-vector' else (1.0,)
        result = replace(result, embeddings=replace(result.embeddings, vectors=(vector, *result.embeddings.vectors[1:])))
    elif mutation == 'wrong-parse-revision':
        result = replace(result, document=replace(result.document, chunks=(
            replace(result.document.chunks[0], parse_revision_id='another-parse'), *result.document.chunks[1:])))
    else:
        chunk = replace(result.document.chunks[1], chunk_id=result.document.chunks[0].chunk_id)
        item = replace(result.translations[1], chunk_id=chunk.chunk_id)
        result = replace(result, document=replace(result.document, chunks=(result.document.chunks[0], chunk)),
                         translations=(result.translations[0], item),
                         embeddings=replace(result.embeddings, chunk_ids=(chunk.chunk_id, chunk.chunk_id)))
    before = deepcopy(result)
    assert_hint(processing_result_advice(result), 'stop', 'processing_failed', None)
    assert result == before


@pytest.mark.parametrize('error', [
    *(ProcessingError(code) for code in SOURCE_CODES + INTERNAL_CODES),
    *(DispatchError(code) for code in DISPATCH_CODES),
    ValueError('private-error'), None,
])
def test_authoritative_unresolved_usage_has_priority_over_later_errors(error):
    assert_hint(processing_error_advice(error, usage_unresolved=True), 'reconcile_usage', 'usage_pending', None)


@pytest.mark.parametrize('status', ['prepared', 'translation_incomplete', 'unrecognized'])
def test_pending_usage_prevents_prepared_or_partial_result_from_clearing_pending(processing_setup, status):
    stages, source, pdf, *_ = processing_setup
    result = replace(stages.run(source, pdf), status=status)
    before = deepcopy(result)
    assert_hint(processing_result_advice(result, usage_unresolved=True), 'reconcile_usage', 'usage_pending', None)
    assert result == before


@pytest.mark.parametrize('invalid', ['false', 'true', 0, 1, [], {}])
def test_invalid_usage_context_cannot_authorize_publication_or_clear_direct_unknown(processing_setup, invalid):
    stages, source, pdf, *_ = processing_setup
    result = stages.run(source, pdf)
    assert_hint(processing_result_advice(result, usage_unresolved=invalid), 'stop', 'processing_failed', None)
    assert_hint(processing_error_advice(ProcessingError('PROCESSING_PDF_MISMATCH'), usage_unresolved=invalid),
                'stop', 'processing_failed', None)
    assert_hint(processing_error_advice(DispatchError('DISPATCH_USAGE_UNKNOWN'), usage_unresolved=invalid),
                'reconcile_usage', 'usage_pending', None)


@pytest.mark.parametrize('context', [False, None])
def test_nonpending_context_never_erases_direct_usage_unknown(context):
    assert_hint(processing_error_advice(DispatchError('DISPATCH_USAGE_UNKNOWN'), usage_unresolved=context),
                'reconcile_usage', 'usage_pending', None)


@pytest.mark.parametrize('mode', ['timeout', 'usage-missing', 'reconcile-error', 'http-error'])
def test_actual_gate_uncertainty_stops_second_chunk_and_next_run_without_second_post(processing_setup, mode):
    stages, source, pdf, ledger, post, response, _ = processing_setup
    if mode == 'timeout':
        post.side_effect = requests.Timeout('private-source secret-key')
    elif mode == 'usage-missing':
        response.json.return_value = {'private': 'private-source secret-key'}
    elif mode == 'reconcile-error':
        def fail(*args):
            raise RuntimeError('private-source secret-key')
        ledger.observe = fail
    else:
        response.status_code = 503
    with pytest.raises(DispatchError) as first:
        stages.run(source, pdf)
    assert_hint(processing_error_advice(first.value), 'reconcile_usage', 'usage_pending', None)
    assert len(ledger.requests) == len(ledger.unknowns) == post.call_count == 1
    with pytest.raises(DispatchError) as second:
        stages.run(source, pdf)
    assert second.value.args == ('DISPATCH_BLOCKED',)
    assert_hint(processing_error_advice(second.value), 'stop', 'processing_failed', None)
    # A가 같은 job/attempt 원장에서 확인한 미정산 상태를 공급하는 합성 예.
    pending = bool(ledger.unknowns)
    assert_hint(processing_error_advice(second.value, usage_unresolved=pending),
                'reconcile_usage', 'usage_pending', None)
    # generic hint does not settle or erase the original pending usage in the ledger.
    assert len(ledger.requests) == len(ledger.unknowns) == post.call_count == 1
    stages.embedder.embed.assert_not_called()


@pytest.mark.parametrize('reason', ['denied', 'authorization-error', 'checkpoint-denied'])
def test_denial_classification_does_not_start_transport_or_invent_consent_cancellation(processing_setup, reason):
    stages, source, pdf, ledger, post, *_ = processing_setup
    if reason == 'denied':
        ledger.allowed = False
    elif reason == 'authorization-error':
        ledger.error = RuntimeError('private-ai-consent')
    else:
        stages.check_allowed.return_value = False
    with pytest.raises((ProcessingError, DispatchError)) as raised:
        stages.run(source, pdf)
    assert_hint(processing_error_advice(raised.value), 'stop', 'processing_failed', None)
    assert ledger.observed == ledger.unknowns == []
    post.assert_not_called()
    stages.embedder.embed.assert_not_called()
