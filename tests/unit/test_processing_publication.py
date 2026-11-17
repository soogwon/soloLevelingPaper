"""게시 전에 JSON을 다시 검사하며 잘못된 입력은 DB에 전달하지 않는다."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from solo_leveling.workers.processing_staging import ProcessingStagingError
from solo_leveling.workers.processing_publication import PostgresStagedProcessingPublisher, ProcessingPublishError
from tests.unit.test_processing_staging import store
from tests.fixtures.processing_result import result


def test_staging_restores_exact_typed_result():
    staging, value = store(), result()
    payload, _, _ = staging._payload(value)
    payload['authorization'] = {}
    assert staging.restore(payload) == value


@pytest.mark.parametrize('change', ['extra', 'missing', 'nan', 'ordinal', 'spec', 'translation'])
def test_recomputed_hash_cannot_replace_result_validation(change):
    staging = store()
    payload, _, _ = staging._payload(result())
    payload = deepcopy(payload)
    payload['authorization'] = {}
    raw = payload['result']
    if change == 'extra': raw['ignored'] = 'unexpected'
    elif change == 'missing': del raw['document']['sections']
    elif change == 'nan': raw['embeddings']['vectors'][0][0] = float('nan')
    elif change == 'ordinal': raw['document']['chunks'][0]['chunk_index'] = True
    elif change == 'spec': raw['embeddings']['spec']['runtime_version'] = 'different'
    else: raw['translations'][0]['text'] = ''
    with pytest.raises(ProcessingStagingError): staging.restore(payload)


def test_invalid_publication_receipt_never_borrows_connection():
    pool = Mock()
    publisher = PostgresStagedProcessingPublisher(store(pool))
    with pytest.raises(ProcessingPublishError):
        publisher.publish(result().source, 'invalid', expected_active_publication_id=None)
    pool.connection.assert_not_called()


@pytest.mark.parametrize('prepared', [True, False])
def test_worker_stops_heartbeat_before_publishing_only_prepared_results(monkeypatch, prepared):
    from dataclasses import replace
    from types import SimpleNamespace as NS
    import solo_leveling.workers.remote_processing as module
    from solo_leveling.interfaces.http.object_storage import DownloadedObject
    value = result()
    if not prepared: value = replace(value, status='translation_incomplete')
    source = value.source
    heartbeat = Mock()
    monkeypatch.setattr(module, '_LeaseHeartbeat', lambda *args, **kwargs: heartbeat)
    jobs = Mock()
    jobs.claim.return_value = NS(lease_token=source.lease_token)
    inputs = Mock()
    inputs.load.return_value = module.RemoteProcessingInput(source, 'private/files/test.pdf', 'application/pdf')
    inputs.allowed.return_value = True
    objects = Mock()
    objects.read_object.return_value = DownloadedObject('private/files/test.pdf', source.pdf_size,
        'application/pdf', source.pdf_sha256, True, b'%PDF-test')
    stages, stage, publisher = Mock(), Mock(), Mock()
    stages.run.return_value = value
    stage.save.return_value = NS(payload_sha256='d' * 64)
    publisher.current.return_value = 'previous'
    def publish(*args, **kwargs):
        heartbeat.stop.assert_called_once()
        assert kwargs == {'expected_active_publication_id': 'previous'}
        assert args == (source, 'd' * 64)
        return 'publication'
    publisher.publish.side_effect = publish
    output = module.RemoteProcessingWorker(jobs, inputs, objects, stages, worker_id='synthetic',
        result_store=stage, publisher=publisher).run_once()
    if prepared: assert output.published == 'publication'
    else:
        publisher.publish.assert_not_called()
        stage.save.assert_not_called()
