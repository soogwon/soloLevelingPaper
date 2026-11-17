"""병합된 #531/#532/#534/#535의 합성 조립. 실제 DB·R2·API·모델 검증이 아니다."""

from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.processing_outcomes import processing_error_advice, processing_result_advice
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.domain.remote_processing import ProcessingError
from solo_leveling.infrastructure.embeddings import processing_embedder as adapter_module
from solo_leveling.interfaces.http.object_storage import DownloadedObject
from solo_leveling.interfaces.http.processing_jobs import ProcessingJob, ProcessingJobLeaseLost
from solo_leveling.workers.remote_processing import (
    RemoteProcessingHeartbeatFailed, RemoteProcessingInput, RemoteProcessingInputRejected,
    RemoteProcessingLeaseLost, RemoteProcessingWorker,
)
from tests.unit.test_processing_embedder import (
    fixture as fixture,
    forbid_network_and_models as forbid_network_and_models,
)
from tests.unit.test_remote_processing_stages import envelope, setup as setup


@pytest.fixture
def harness(request):
    stages, source, pdf, ledger, post, response, _ = request.getfixturevalue('setup')
    adapter, spec, _, model, factory = request.getfixturevalue('fixture')
    source = replace(source, lease_token='synthetic-lease')
    claimed = ProcessingJob(source.job_id, source.owner_user_id, source.paper_id, source.version_id,
        'full_processing', 'synthetic-request', source.pdf_sha256, 'running', 1, 3, False, source.lease_token)
    state = dict(allowed=True, fail_heartbeat=None)
    heartbeat_ready, heartbeat_failed = Event(), Event()

    class Jobs:
        def __init__(self): self.claims = 0
        def claim(self, *args, **kwargs):
            self.claims += 1
            return claimed if self.claims == 1 else None
        def heartbeat(self, owner, job, token, **kwargs):
            assert (owner, job, token) == (source.owner_user_id, source.job_id, source.lease_token)
            if heartbeat_ready.is_set() and state['fail_heartbeat'] is not None:
                state['allowed'] = False
                heartbeat_failed.set()
                raise state['fail_heartbeat']
        def succeed(self, *args, **kwargs): pytest.fail('prepared must not mark job succeeded')

    class Inputs:
        def load(self, job):
            assert job == claimed
            return RemoteProcessingInput(source, 'private/files/synthetic.pdf', 'application/pdf')
        def allowed(self, current, phase):
            assert current == source and phase in ('parse', 'translation', 'embedding', 'result')
            return state['allowed'] and not ledger.unknowns

    class Objects:
        def read_object(self, key, *, expected_size):
            assert expected_size == len(pdf)
            return DownloadedObject(key, len(pdf), 'application/pdf', source.pdf_sha256, True, pdf)

    inputs, jobs = Inputs(), Jobs()
    stages.check_allowed = inputs.allowed
    stages.spec = spec
    stages.embedder = replace(adapter, check_allowed=lambda: inputs.allowed(source, 'embedding'))
    worker = RemoteProcessingWorker(jobs, inputs, Objects(), stages, worker_id='synthetic-worker',
        lease_seconds=1, heartbeat_interval_seconds=0.01)
    return worker, claimed, ledger, post, response, state, model, factory, heartbeat_ready, heartbeat_failed


def test_claimed_prepared_remains_unpublished_after_all_components_are_composed(harness):
    worker, job, ledger, post, _, _, _, factory, *_ = harness
    result = worker.run_once()
    assert result.job == job and job.status == 'running'
    assert result.result.status == 'prepared'
    advice = processing_result_advice(result.result)
    assert advice.action == 'await_publication' and advice.requires_authoritative_review
    assert advice.error_code is None and advice.retryable is None
    assert result.result.source.lease_token == job.lease_token
    assert len(ledger.observed) == post.call_count == 2 and factory.call_count == 1
    # This in-memory queue returns no second job; not a PostgreSQL idempotency proof.
    assert worker.run_once() is None
    assert factory.call_count == 1 and post.call_count == 2


@pytest.mark.parametrize('mode', ['timeout', 'http-503', 'usage-missing'])
def test_worker_usage_uncertainty_reaches_classifier_and_never_loads_model(harness, mode):
    worker, job, ledger, post, response, _, model, factory, *_ = harness
    if mode == 'timeout': post.side_effect = requests.Timeout('private synthetic timeout')
    elif mode == 'http-503': response.status_code = 503
    else: response.json.return_value = {'status': 'completed'}
    with pytest.raises(DispatchError) as error: worker.run_once()
    advice = processing_error_advice(error.value, usage_unresolved=bool(ledger.unknowns))
    assert advice.error_code == 'usage_pending' and advice.retryable is None
    assert len(ledger.requests) == len(ledger.unknowns) == post.call_count == 1
    factory.assert_not_called()
    model.encoder.assert_not_called()
    assert job.status == 'running'


def test_worker_partial_translation_stays_unpublished_without_embedding(harness):
    worker, job, _, _, response, _, model, factory, *_ = harness
    response.json.return_value = envelope('')
    worker.result_store = Mock()
    result = worker.run_once()
    worker.result_store.save.assert_not_called()
    assert result.job == job and result.result.status == 'translation_incomplete'
    assert result.result.embeddings is None
    advice = processing_result_advice(result.result)
    assert advice.action == 'stop' and advice.error_code == 'processing_failed' and advice.retryable is None
    factory.assert_not_called()
    model.encoder.assert_not_called()


def test_worker_rejected_input_cannot_translate_or_load_model(harness):
    worker, _, ledger, post, _, state, _, factory, *_ = harness
    state['allowed'] = False
    with pytest.raises(RemoteProcessingInputRejected): worker.run_once()
    assert ledger.requests == []
    post.assert_not_called()
    factory.assert_not_called()


def test_current_input_denial_during_adapter_hash_blocks_loading(harness, monkeypatch):
    worker, _, _, _, _, state, model, factory, *_ = harness
    original = adapter_module.processing_artifact_sha256
    def hash_then_revoke(folder):
        digest = original(folder)
        state['allowed'] = False
        return digest
    monkeypatch.setattr(adapter_module, 'processing_artifact_sha256', hash_then_revoke)
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'): worker.run_once()
    factory.assert_not_called()
    model.encoder.assert_not_called()


@pytest.mark.parametrize('failure_type,expected', [
    (ProcessingJobLeaseLost, RemoteProcessingLeaseLost), (RuntimeError, RemoteProcessingHeartbeatFailed),
])
def test_heartbeat_failure_and_input_denial_discard_result_without_model_load(harness, monkeypatch, failure_type, expected):
    worker, _, _, _, _, state, model, factory, ready, failed = harness
    original = adapter_module.processing_artifact_sha256
    state['fail_heartbeat'] = failure_type('private synthetic driver detail')
    def hash_then_wait(folder):
        digest = original(folder)
        ready.set()
        assert failed.wait(2), 'synthetic heartbeat did not run'
        return digest
    monkeypatch.setattr(adapter_module, 'processing_artifact_sha256', hash_then_wait)
    with pytest.raises(expected) as error: worker.run_once()
    assert 'private' not in str(error.value)
    assert not state['allowed']
    factory.assert_not_called()
    model.encoder.assert_not_called()


@pytest.mark.parametrize('failure_type,expected', [
    (ProcessingJobLeaseLost, RemoteProcessingLeaseLost), (RuntimeError, RemoteProcessingHeartbeatFailed),
])
def test_heartbeat_exception_does_not_erase_usage_already_marked_unknown(harness, failure_type, expected):
    worker, _, ledger, post, _, state, model, factory, ready, failed = harness
    state['fail_heartbeat'] = failure_type('private synthetic heartbeat detail')
    def timeout(*args, **kwargs):
        ready.set()
        assert failed.wait(2), 'synthetic heartbeat did not run'
        raise requests.Timeout('private synthetic provider detail')
    post.side_effect = timeout
    with pytest.raises(expected) as error: worker.run_once()
    # The worker prioritizes stale-generation rejection. Classification must consult this job's ledger,
    # rather than infer zero usage or a refundable provider failure from the outer heartbeat exception.
    advice = processing_error_advice(error.value, usage_unresolved=bool(ledger.unknowns))
    assert advice.error_code == 'usage_pending' and advice.retryable is None
    assert len(ledger.requests) == len(ledger.unknowns) == post.call_count == 1
    assert ledger.observed == []
    factory.assert_not_called()
    model.encoder.assert_not_called()
