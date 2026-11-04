"""실제 등록 흐름에서 #60 상태 계약 다섯 키가 상태 조회·등록 결과에 같은 값으로 나오는지 확인한다."""
import pytest

from solo_leveling.application.translation.ports import TranslationProviderUnavailable
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.embeddings.process_embedder import EmbeddingFailure, EmbeddingProcessError
from solo_leveling.infrastructure.parsing.pdf_extractor import PdfExtractionError
from solo_leveling.workers import ingestion as ingestion_module
from solo_leveling.workers.ingestion import get_ingestion_status, prepare_local_ingestion, run_prepared_local_ingestion
from tests.fixtures.pdf_builder import build_minimal_pdf, write_minimal_pdf
from tests.integration.test_ingestion import FixtureProvider, mock_embeddings, register_and_ingest  # noqa: F401

KEYS = ('failed_stage', 'failure_code', 'retryable', 'next_action', 'outcome')


def contract(result):
    return tuple(result[k] for k in KEYS)


def paths(tmp_path):
    return str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma')


def test_clean_ready_is_complete_with_null_failure_fields(tmp_path):
    pdf = tmp_path / 'ok.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)
    result = register_and_ingest(db, vectors, str(pdf))
    assert contract(result) == (None, None, None, None, 'complete')
    assert contract(get_ingestion_status(db, result['job_id'])) == contract(result)


def test_partial_translation_is_partial_without_recovery_action(tmp_path):
    class PartialProvider(FixtureProvider):
        def translate(self, request, settings):
            if request.original_text == 'Second page.':
                raise TranslationProviderUnavailable()
            return super().translate(request, settings)

    pdf = tmp_path / 'partial.pdf'
    write_minimal_pdf(str(pdf), ['First page.', 'Second page.'])
    db, vectors = paths(tmp_path)
    result = register_and_ingest(db, vectors, str(pdf), translation_service=TranslationService(PartialProvider()))
    assert result['status'] == 'ready'
    assert result['failed_stage'] == 'translate' and result['retryable'] is None
    assert (result['next_action'], result['outcome']) == (None, 'partial')
    assert result['failure_code'] and 'private' not in result['failure_code']
    assert contract(get_ingestion_status(db, result['job_id'])) == contract(result)


def test_all_translation_failure_allows_register_again(tmp_path):
    pdf = tmp_path / 'fail.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)
    result = register_and_ingest(db, vectors, str(pdf), translation_service=TranslationService(FixtureProvider(fail=True)))
    assert result['status'] == 'failed'
    assert contract(result) == ('translate', 'translation_failed', True, 'register_again', 'failed')
    assert contract(get_ingestion_status(db, result['job_id'])) == contract(result)


def test_empty_extraction_asks_to_replace_file(tmp_path):
    pdf = tmp_path / 'empty.pdf'
    write_minimal_pdf(str(pdf), [''])
    db, vectors = paths(tmp_path)
    result = register_and_ingest(db, vectors, str(pdf))
    assert contract(result) == ('parse', 'extraction_empty', False, 'replace_file', 'failed')
    assert contract(get_ingestion_status(db, result['job_id'])) == contract(result)


def test_unreadable_pdf_status_asks_to_replace_file(tmp_path):
    data = build_minimal_pdf(['Attention is all you need.'])
    pdf = tmp_path / 'truncated.pdf'
    pdf.write_bytes(data[: int(len(data) * 0.55)])
    db, vectors = paths(tmp_path)
    with pytest.raises(PdfExtractionError):
        register_and_ingest(db, vectors, str(pdf))
    with repo.get_connection(db) as conn:
        job_id = conn.execute('SELECT job_id FROM processing_jobs').fetchone()[0]
    assert contract(get_ingestion_status(db, job_id)) == ('parse', 'pdf_unreadable', False, 'replace_file', 'failed')


@pytest.mark.parametrize('failure,expected', [
    (EmbeddingFailure.TIMEOUT, ('index', 'EMBEDDING_TIMEOUT', True, 'register_again', 'failed')),
    (EmbeddingFailure.UNAVAILABLE, ('index', 'EMBEDDING_UNAVAILABLE', True, 'register_again', 'failed')),
    (EmbeddingFailure.INVALID_RESULT, ('index', 'EMBEDDING_INVALID_RESULT', False, None, 'failed')),
])
def test_embedding_failures_map_to_index_stage(tmp_path, failure, expected):
    pdf = tmp_path / 'e.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)

    def embed(texts, *, model_name):
        raise EmbeddingProcessError(failure)

    prepared = prepare_local_ingestion(db, vectors, str(pdf))
    with pytest.raises(EmbeddingProcessError):
        run_prepared_local_ingestion(
            prepared, translation_service=TranslationService(FixtureProvider()),
            translation_settings=TranslationSettings('fake', 'fixture', 'prompt1'), embedder=embed)
    with repo.get_connection(db) as conn:
        job_id = conn.execute('SELECT job_id FROM processing_jobs').fetchone()[0]
    status = get_ingestion_status(db, job_id)
    assert status['status'] == 'failed' and contract(status) == expected


def test_unclassified_exception_uses_fallback_without_leaking_message(tmp_path, monkeypatch):
    pdf = tmp_path / 'x.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)

    def boom(*args, **kwargs):
        raise ValueError('private index details /Users/x/secret')

    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', boom)
    with pytest.raises(ValueError):
        register_and_ingest(db, vectors, str(pdf))
    with repo.get_connection(db) as conn:
        job_id = conn.execute('SELECT job_id FROM processing_jobs').fetchone()[0]
    status = get_ingestion_status(db, job_id)
    assert contract(status) == ('index', 'ingestion_failed', True, 'register_again', 'failed')
    assert 'private' not in str(status)


def test_processing_job_has_all_five_keys_as_null(tmp_path):
    pdf = tmp_path / 'p.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)
    prepared = prepare_local_ingestion(db, vectors, str(pdf))
    status = get_ingestion_status(db, prepared.registration.job_id)
    assert status['status'] == 'processing' and contract(status) == (None, None, None, None, None)
    assert all(k in status for k in KEYS)


def test_interrupted_job_asks_to_register_again(tmp_path):
    pdf = tmp_path / 'i.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)
    prepared = prepare_local_ingestion(db, vectors, str(pdf))
    job_id = prepared.registration.job_id
    assert repo.mark_job_cancelled_before_start(db, job_id)
    assert contract(get_ingestion_status(db, job_id)) == ('parse', 'SERVER_SHUTTING_DOWN', True, 'register_again', 'failed')
    # 재시작 시 interrupted로 바뀐 작업은 limitations가 비어 있어도 fallback 코드를 쓴다
    with repo.get_connection(db) as conn:
        conn.execute("UPDATE processing_jobs SET limitations='[]' WHERE job_id=?", (job_id,))
    assert contract(get_ingestion_status(db, job_id))[1] == 'interrupted'


def test_same_request_key_returns_the_failed_job_not_in_progress_and_new_key_starts_new_job(tmp_path):
    pdf = tmp_path / 'k.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = paths(tmp_path)
    service = TranslationService(FixtureProvider(fail=True))
    settings = TranslationSettings('fake', 'fixture', 'prompt1')
    first = prepare_local_ingestion(db, vectors, str(pdf), request_key='key-1', input_fingerprint='fp')
    failed = run_prepared_local_ingestion(first, translation_service=service, translation_settings=settings)
    assert failed['status'] == 'failed'
    # 같은 키: 새 작업을 만들지 않고 이전 실패 작업을 그대로 보여 준다(처리 중으로 보이지 않는다).
    same = prepare_local_ingestion(db, vectors, str(pdf), request_key='key-1', input_fingerprint='fp')
    assert same.registration.job_id == first.registration.job_id
    shown = run_prepared_local_ingestion(same, translation_service=service, translation_settings=settings)
    assert shown['status'] == 'failed' and shown['reused_existing'] is True
    assert contract(shown) == contract(failed) == ('translate', 'translation_failed', True, 'register_again', 'failed')
    # 새 키: 실패 이력을 남기고 새 작업을 시작한다.
    fresh = prepare_local_ingestion(db, vectors, str(pdf), request_key='key-2', input_fingerprint='fp')
    assert fresh.registration.job_id != first.registration.job_id
    assert get_ingestion_status(db, first.registration.job_id)['status'] == 'failed'
