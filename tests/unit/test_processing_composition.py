"""제품 조립을 합성 PDF·전송·모델로 실행하며 실제 API는 차단한다."""
from dataclasses import replace
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.provider_dispatch import DispatchIntent, DispatchError
from solo_leveling.domain.remote_processing import ProcessingError
from solo_leveling.workers.processing_composition import ProcessingStagesFactory
from tests.unit.test_processing_embedder import fixture as fixture, forbid_network_and_models as forbid_network_and_models  # noqa: F401
from tests.unit.test_remote_processing_stages import setup as setup  # noqa: F401


@pytest.fixture
def composed(request):
    stages, source, pdf, ledger, post, response, service = request.getfixturevalue('setup')
    adapter, spec, _, model, model_factory = request.getfixturevalue('fixture')
    source = replace(source, lease_token='lease')
    auth = Mock()
    auth.dispatch_intent.side_effect = lambda s: DispatchIntent(s.owner_user_id, s.job_id, s.lease_token,
                                                               'hash', 'storage', 'ai', 'notice')
    auth.ledger.return_value = ledger
    estimate = Mock(return_value=1000)
    factory = ProcessingStagesFactory(auth, service_config=service.provider.config,
        model_dir=adapter.model_dir, embedding_spec=spec, limits=stages.limits,
        retry_policy=service.retry_policy, policy_version='policy', window_key='window',
        token_upper_bound=estimate, batch_size=8, post=post, model_factory=model_factory)
    check = Mock(return_value=True)
    return factory, source, pdf, ledger, post, response, model_factory, estimate, check


def test_server_composition_runs_parser_translation_and_fixed_model(composed):
    factory, source, pdf, ledger, post, _, model_factory, estimate, check = composed
    result = factory(source, check).run(source, pdf)
    assert result.status == 'prepared'
    assert len(result.translations) == post.call_count == len(ledger.observed) == 2
    assert result.embeddings.spec == factory.spec
    assert model_factory.call_count == 1
    assert all(call.args[0] == factory.config.translation_settings.model for call in estimate.call_args_list)
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer synthetic-service-secret'
    assert all(r.intent.execution_id == source.job_id and r.intent.lease_token == source.lease_token
               for r in ledger.requests)
    factory.authorization.ledger.assert_called_once_with(source, check, policy_version='policy', window_key='window')


@pytest.mark.parametrize('mode', ['timeout', 'unknown', 'denied'])
def test_composition_never_loads_model_after_provider_uncertainty_or_denial(composed, mode):
    factory, source, pdf, ledger, post, response, model_factory, _, check = composed
    if mode == 'timeout': post.side_effect = requests.Timeout('private')
    elif mode == 'unknown': response.json.return_value = {'status': 'completed'}
    else: ledger.allowed = False
    with pytest.raises(DispatchError): factory(source, check).run(source, pdf)
    model_factory.assert_not_called()
    assert post.call_count == (0 if mode == 'denied' else 1)
    assert len(ledger.unknowns) == (0 if mode == 'denied' else 1)


def test_reclaimed_revision_and_lease_keep_same_logical_dispatch_keys(composed):
    factory, source, pdf, ledger, _, _, _, _, check = composed
    factory(source, check).run(source, pdf)
    first = [r.dispatch_key for r in ledger.requests]
    replacement = replace(source, parse_revision_id='new-parse', translation_revision_id='new-translation',
                          embedding_set_id='new-set', lease_token='new-lease')
    factory(replacement, check).run(replacement, pdf)
    assert [r.dispatch_key for r in ledger.requests[2:]] == first
    assert len(set(first)) == 2


@pytest.mark.parametrize('upper', [None, True, 0, -1, '100'])
def test_missing_trusted_token_bound_never_reserves_or_transmits(composed, upper):
    factory, source, pdf, ledger, post, _, model_factory, estimate, check = composed
    estimate.return_value = upper
    with pytest.raises(DispatchError): factory(source, check).run(source, pdf)
    assert ledger.requests == []
    post.assert_not_called()
    model_factory.assert_not_called()


def test_foreign_intent_rejected_before_ledger_or_model(composed):
    factory, source, _, _, post, _, model_factory, _, check = composed
    factory.authorization.dispatch_intent.side_effect = None
    factory.authorization.dispatch_intent.return_value = DispatchIntent('other', 'job', 'lease', 'hash', 's', 'a', 'n')
    with pytest.raises(ProcessingError): factory(source, check)
    factory.authorization.ledger.assert_not_called()
    post.assert_not_called()
    model_factory.assert_not_called()


def test_embedding_rechecks_same_worker_callback(composed):
    factory, source, pdf, _, _, _, model_factory, _, check = composed
    check.side_effect = lambda current, phase: phase != 'embedding'
    with pytest.raises(ProcessingError): factory(source, check).run(source, pdf)
    model_factory.assert_not_called()


def test_worker_runs_scoped_composition_without_publishing(composed):
    from solo_leveling.interfaces.http.object_storage import DownloadedObject
    from solo_leveling.interfaces.http.processing_jobs import ProcessingJob
    from solo_leveling.workers.remote_processing import RemoteProcessingInput, RemoteProcessingWorker

    factory, source, pdf, ledger, post, _, model_factory, _, _ = composed
    job = ProcessingJob(source.job_id, source.owner_user_id, source.paper_id, source.version_id,
        'full_processing', 'request', source.pdf_sha256, 'running', 1, 3, False, source.lease_token)
    jobs, inputs, objects = Mock(), Mock(), Mock()
    jobs.claim.return_value = job
    inputs.load.return_value = RemoteProcessingInput(source, 'synthetic.pdf', 'application/pdf')
    inputs.allowed.return_value = True
    objects.read_object.return_value = DownloadedObject('synthetic.pdf', len(pdf),
        'application/pdf', source.pdf_sha256, True, pdf)
    worker = RemoteProcessingWorker(jobs, inputs, objects, None, worker_id='synthetic',
        lease_seconds=30, stages_factory=factory)
    result = worker.run_once()
    assert result.result.status == 'prepared' and result.job == job
    assert post.call_count == len(ledger.observed) == 2
    assert model_factory.call_count == 1
    assert {'parse', 'translation', 'embedding', 'result'} <= {
        call.args[1] for call in inputs.allowed.call_args_list}
    assert all(call.args[0] == source for call in inputs.allowed.call_args_list)
    jobs.succeed.assert_not_called()
