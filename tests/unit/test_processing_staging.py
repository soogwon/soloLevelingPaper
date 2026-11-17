"""잘못된 처리 결과는 DB 접근 전에 거부하며 예외 본문을 공개하지 않는다."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from solo_leveling.workers.processing_staging import PostgresProcessingResultStaging, ProcessingStagingError
from tests.fixtures.processing_result import result, LIMITS, SETTINGS, SPEC


def store(pool=None, max_bytes=100_000):
    return PostgresProcessingResultStaging(pool or Mock(), limits=LIMITS, translation_settings=SETTINGS,
                                         embedding_spec=SPEC, max_result_bytes=max_bytes)


def test_preserves_all_metadata_and_normalizes_negative_zero():
    payload, encoded, digest = store()._payload(result())
    data = payload['result']
    assert data['document']['chunks'][0]['printed_page_label'] == 'i'
    assert data['document']['sections'][0]['title'] == 'Synthetic'
    assert data['translation_settings']['prompt_version'] == 'v1'
    assert data['empty_pages'] == (2,)
    assert b'-0.0' not in encoded
    assert len(digest) == 64


@pytest.mark.parametrize('change', ['incomplete', 'settings', 'spec', 'nan', 'zero', 'dimension',
                                   'chunk', 'translation', 'empty', 'revision', 'source', 'size', 'section'])
def test_invalid_results_never_borrow_database(change):
    value = result()
    if change == 'incomplete': value = replace(value, status='translation_incomplete')
    elif change == 'settings': value = replace(value, translation_settings=replace(SETTINGS, model='other'))
    elif change == 'spec': value = replace(value, embeddings=replace(value.embeddings, spec=replace(SPEC, runtime_version='other')))
    elif change in ('nan', 'zero', 'dimension'):
        row = {'nan': (float('nan'), 0), 'zero': (0, 0), 'dimension': (1,)}[change]
        value = replace(value, embeddings=replace(value.embeddings, vectors=(row,)))
    elif change == 'chunk': value = replace(value, embeddings=replace(value.embeddings, chunk_ids=('other',)))
    elif change == 'translation': value = replace(value, translations=(replace(value.translations[0], text=''),))
    elif change == 'empty': value = replace(value, empty_pages=())
    elif change == 'revision': value = replace(value, source=replace(value.source, parse_revision_id=value.source.job_id))
    elif change == 'source': value = replace(value, source=replace(value.source, lease_token=None))
    elif change == 'size': value = replace(value, source=replace(value.source, pdf_size=100_001))
    else: value = replace(value, document=replace(value.document, sections=(replace(value.document.sections[0], title=''),)))
    pool = Mock()
    with pytest.raises(ProcessingStagingError, match='^processing_staging_invalid$'): store(pool).save(value)
    pool.connection.assert_not_called()


def test_payload_limit_and_database_exception_are_safe():
    pool = Mock()
    with pytest.raises(ProcessingStagingError, match='^processing_staging_invalid$'): store(pool, 1).save(result())
    pool.connection.assert_not_called()
    pool.connection.side_effect = RuntimeError('private password and original text')
    with pytest.raises(ProcessingStagingError, match='^processing_staging_unavailable$'): store(pool).save(result())
