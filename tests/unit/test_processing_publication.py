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
