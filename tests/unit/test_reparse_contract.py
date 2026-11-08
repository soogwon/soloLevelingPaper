"""#116 기존 논문의 새 파싱 리비전 저장·게시 계약을 검증한다."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from solo_leveling.domain.models import (
    Chunk, EmbeddingSet, Evidence, JobStage, JobStatus, LearningContext, Paper, PaperVersion,
    ParseRevision, ProcessingJob, ReparseDisposition, TranslationRevision,
)
from solo_leveling.domain.translation import (
    ChunkTranslationResult, TranslationBatchResult, TranslationRequest, TranslationSettings,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db


SETTINGS = TranslationSettings('provider', 'model', 'prompt')


def translated_batch(revision_id, parse_revision_id, chunks):
    items = tuple(ChunkTranslationResult(
        TranslationRequest(chunk.chunk_id, parse_revision_id, chunk.original_text),
        f'번역 {chunk.chunk_id}',
    ) for chunk in chunks)
    return TranslationBatchResult(
        TranslationRevision(revision_id, parse_revision_id, 'provider', 'model'), SETTINGS, items)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'reparse.db')
    init_db(path)
    repo.insert_paper(path, Paper('paper'))
    repo.insert_version(path, PaperVersion('v1', 'paper', 'hash', 'paper.pdf', 'paper.pdf'))
    repo.insert_parse_revision(path, ParseRevision('p1', 'v1', 1))
    chunks = [Chunk('old-1', 'p1', 0, 'Old first.'), Chunk('old-2', 'p1', 1, 'Old second.')]
    repo.insert_chunks(path, chunks)
    repo.save_translation_batch(path, translated_batch('t1', 'p1', chunks))
    repo.insert_job(path, ProcessingJob('initial', 'v1', JobStatus.PROCESSING, JobStage.INDEX))
    repo.publish_search_index(path, 'v1', 'p1', EmbeddingSet('idx1', 't1', 'embed', 2),
                              'initial', [chunk.chunk_id for chunk in chunks], [])
    return path


def job(job_id='reparse'):
    return ProcessingJob(job_id, 'v1', JobStatus.PROCESSING, JobStage.PARSE)


def prepare(db, job_id='reparse', request_key='request'):
    return repo.prepare_reparse(db, job(job_id), request_key, 'input-v1', 'p1', 'idx1')


def new_chunks(parse_revision_id='p2'):
    return [Chunk('new-1', parse_revision_id, 0, 'New first.'),
            Chunk('new-2', parse_revision_id, 1, 'New second.')]


def test_reparse_request_is_idempotent_and_rejects_changed_input(db):
    accepted = prepare(db)
    assert accepted.disposition == ReparseDisposition.ACCEPTED
    replay = prepare(db, 'unused')
    assert replay.disposition == ReparseDisposition.IN_PROGRESS
    assert replay.job_id == accepted.job_id
    conflict = repo.prepare_reparse(db, job('unused'), 'request', 'changed', 'p1', 'idx1')
    assert conflict.disposition == ReparseDisposition.REQUEST_CONFLICT
    assert conflict.job_id is None


def test_concurrent_reparse_requests_share_one_active_job(db):
    def register(number):
        return prepare(db, f'job-{number}', f'request-{number}')

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, (1, 2)))
    assert {result.disposition for result in results} == {
        ReparseDisposition.ACCEPTED, ReparseDisposition.IN_PROGRESS}
    assert len({result.job_id for result in results}) == 1


def test_reparse_rejects_changed_or_unpublished_base(db):
    changed = repo.prepare_reparse(db, job(), 'request', 'input', 'p1', 'other')
    assert changed.disposition == ReparseDisposition.BASE_CHANGED
    changed_parse = repo.prepare_reparse(db, job(), 'request-2', 'input', 'other', 'idx1')
    assert changed_parse.disposition == ReparseDisposition.BASE_CHANGED


def test_new_parse_is_atomic_and_preserves_old_chunks(db):
    prepare(db)
    chunks = new_chunks()
    repo.save_reparse_result(db, 'reparse', ParseRevision('p2', 'v1', 1), chunks, [])
    assert [chunk.chunk_id for chunk in repo.get_chunks_by_parse_revision(db, 'p1')] == ['old-1', 'old-2']
    assert [chunk.chunk_id for chunk in repo.get_chunks_by_parse_revision(db, 'p2')] == ['new-1', 'new-2']
    replay = prepare(db, 'unused')
    assert replay.new_parse_revision_id == 'p2'
    with pytest.raises(ValueError, match='another parse revision'):
        repo.save_reparse_result(db, 'reparse', ParseRevision('p3', 'v1', 1),
                                 new_chunks('p3'), [])
    assert repo.get_chunks_by_parse_revision(db, 'p3') == []


@pytest.mark.parametrize('bad_chunk', [
    Chunk('bad', 'p2', 1, 'Wrong index.'),
    Chunk('bad', 'p2', 0, 'Already translated.', text='번역', translation_revision_id='t1'),
    Chunk('bad', 'p2', 0, 'Outside page.', pdf_page=2),
])
def test_new_parse_rejects_invalid_or_pretranslated_chunks_atomically(db, bad_chunk):
    prepare(db)
    with pytest.raises(ValueError, match='uniquely belong'):
        repo.save_reparse_result(db, 'reparse', ParseRevision('p2', 'v1', 1), [bad_chunk], [])
    assert repo.get_chunks_by_parse_revision(db, 'p2') == []


def test_failed_reparse_keeps_existing_default_index(db):
    prepare(db)
    repo.save_reparse_result(db, 'reparse', ParseRevision('p2', 'v1', 1), new_chunks(), [])
    repo.fail_reparse(db, 'reparse', ['TRANSLATION_FAILED'])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'idx1'
    replay = prepare(db, 'unused')
    assert replay.disposition == ReparseDisposition.FAILED
    assert replay.new_parse_revision_id == 'p2'


def test_publish_switches_only_new_default_and_keeps_old_index(db):
    repo.create_learning_context(db, LearningContext(
        'old-context', 'v1', 'p1', 't1', embedding_set_id='idx1'))
    repo.save_evidences(db, 'old-context', [Evidence(
        'old-evidence', 'old-1', '번역 old-1', 'Old first.')])
    prepare(db)
    chunks = new_chunks()
    repo.save_reparse_result(db, 'reparse', ParseRevision('p2', 'v1', 1), chunks, [])
    repo.save_translation_batch(db, translated_batch('t2', 'p2', chunks))
    repo.publish_reparsed_index(db, 'reparse', EmbeddingSet('idx2', 't2', 'embed', 2),
                                [chunk.chunk_id for chunk in chunks], [])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'idx2'
    assert repo.get_search_index(db, 'v1', embedding_set_id='idx1')['parse_revision_id'] == 'p1'
    assert [chunk.chunk_id for chunk in repo.get_chunks_by_parse_revision(db, 'p1')] == ['old-1', 'old-2']
    old_evidence = repo.get_evidences(db, 'old-context', ['old-evidence']).evidence[0]
    assert old_evidence.chunk_id == 'old-1'
    assert old_evidence.quote_original == 'Old first.'
    assert old_evidence.quote_ko == '번역 old-1'
    replay = prepare(db, 'unused')
    assert replay.disposition == ReparseDisposition.COMPLETED
    assert replay.new_parse_revision_id == 'p2'


def test_publish_rejects_default_index_race(db):
    prepare(db)
    chunks = new_chunks()
    repo.save_reparse_result(db, 'reparse', ParseRevision('p2', 'v1', 1), chunks, [])
    repo.save_translation_batch(db, translated_batch('t2', 'p2', chunks))
    with repo.get_connection(db) as conn:
        conn.execute("UPDATE search_indexes SET is_default=0 WHERE embedding_set_id='idx1'")
        conn.execute("INSERT INTO embedding_sets VALUES ('changed', 't1', 'embed', 2, 'now')")
        conn.execute("INSERT INTO search_indexes (embedding_set_id, version_id, parse_revision_id, "
                     "translation_revision_id, job_id, chunk_count, is_default) "
                     "VALUES ('changed', 'v1', 'p1', 't1', 'initial', 2, 1)")
    with pytest.raises(ValueError, match='default index changed'):
        repo.publish_reparsed_index(db, 'reparse', EmbeddingSet('idx2', 't2', 'embed', 2),
                                    [chunk.chunk_id for chunk in chunks], [])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'changed'
