"""#30 후속: 최초 등록의 번역 판단은 chunks.text가 아니라 translation_results를 기준으로 한다."""
from contextlib import closing

import pytest

from solo_leveling.domain.models import (
    Chunk, EmbeddingSet, JobStage, JobStatus, Paper, PaperVersion, ParseRevision, ProcessingJob, TranslationRevision,
)
from solo_leveling.domain.translation import (
    ChunkTranslationResult, TranslationBatchResult, TranslationFailureCode, TranslationRequest, TranslationSettings,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db

SETTINGS = TranslationSettings('p', 'm', 'v1')


def req(i):
    return TranslationRequest(f'c{i}', 'p1', f'original {i}')


def ok(i, text=None):
    return ChunkTranslationResult(req(i), text or f'번역 {i}')


def fail(i):
    return ChunkTranslationResult(req(i), None, TranslationFailureCode.PROVIDER_UNAVAILABLE)


def batch(items, rev='t1'):
    return TranslationBatchResult(TranslationRevision(rev, 'p1', 'p', 'm'), SETTINGS, tuple(items))


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'a.db')
    init_db(path)
    repo.insert_paper(path, Paper('paper'))
    repo.insert_version(path, PaperVersion('v1', 'paper', 'h', 'x.pdf', 'x.pdf'))
    repo.insert_parse_revision(path, ParseRevision('p1', 'v1', 1))
    repo.insert_chunks(path, [Chunk(f'c{i}', 'p1', i, f'original {i}') for i in range(4)])
    repo.insert_job(path, ProcessingJob('j1', 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE))
    return path


def sql(db, query, *args):
    with closing(get_connection(db)) as conn, conn:
        return [dict(r) for r in conn.execute(query, args)]


def ids(chunks):
    return [c.chunk_id for c in chunks]


def test_pending_is_all_chunks_before_any_translation(db):
    assert ids(repo.get_pending_translation_chunks(db, 'p1', None)) == ['c0', 'c1', 'c2', 'c3']


def test_pending_is_failed_chunks_only_after_partial_translation(db):
    repo.save_translation_batch(db, batch([ok(0), fail(1), ok(2), fail(3)]))
    assert ids(repo.get_pending_translation_chunks(db, 'p1', 't1')) == ['c1', 'c3']


def test_pending_ignores_chunks_text_even_if_it_disagrees(db):
    repo.save_translation_batch(db, batch([ok(0), fail(1), ok(2), ok(3)]))
    sql(db, 'UPDATE chunks SET text=NULL, translation_revision_id=NULL')  # 사본이 비어도
    assert ids(repo.get_pending_translation_chunks(db, 'p1', 't1')) == ['c1']
    sql(db, "UPDATE chunks SET text='아무거나' WHERE chunk_id='c1'")  # 사본이 차 있어도
    assert ids(repo.get_pending_translation_chunks(db, 'p1', 't1')) == ['c1']


def test_resave_identical_batch_is_idempotent_without_chunks_text(db):
    repo.save_translation_batch(db, batch([ok(0), fail(1)]))
    sql(db, 'UPDATE chunks SET text=NULL, translation_revision_id=NULL')
    repo.save_translation_batch(db, batch([ok(0), fail(1)]))  # 사본이 없어도 같은 내용이면 그대로
    assert sql(db, "SELECT translated_text FROM translation_results WHERE chunk_id='c0'")[0]['translated_text'] == '번역 0'


def test_successful_translation_cannot_be_overwritten_even_when_copy_is_missing(db):
    repo.save_translation_batch(db, batch([ok(0)]))
    sql(db, 'UPDATE chunks SET text=NULL, translation_revision_id=NULL')
    with pytest.raises(ValueError, match='cannot be overwritten'):
        repo.save_translation_batch(db, batch([ok(0, '다른 번역')]))
    assert sql(db, "SELECT translated_text FROM translation_results WHERE chunk_id='c0'")[0]['translated_text'] == '번역 0'


def test_failed_chunk_can_be_filled_on_retry(db):
    repo.save_translation_batch(db, batch([ok(0), fail(1)]))
    repo.save_translation_batch(db, batch([ok(1)]))
    assert ids(repo.get_pending_translation_chunks(db, 'p1', 't1')) == ['c2', 'c3']


def test_publish_counts_successes_from_translation_results(db):
    repo.save_translation_batch(db, batch([ok(0), ok(1), fail(2), fail(3)]))
    sql(db, 'UPDATE chunks SET text=NULL, translation_revision_id=NULL')  # 사본 없이도 게시 검증이 된다
    emb = EmbeddingSet('e1', 't1', 'model', 4)
    with pytest.raises(ValueError, match='does not match'):
        repo.publish_search_index(db, 'v1', 'p1', emb, 'j1', 3, [])  # 성공 수와 다르면 거부
    repo.publish_search_index(db, 'v1', 'p1', emb, 'j1', 2, ['partial'])
    assert repo.get_search_index(db, 'v1')['chunk_count'] == 2


def test_migration_backfills_legacy_success_without_result_row(db):
    repo.save_translation_batch(db, batch([ok(0), ok(1)]))
    sql(db, "DELETE FROM translation_results WHERE chunk_id='c1'")  # 옛 DB: chunks.text만 있고 결과 행이 없음
    init_db(db)
    row = sql(db, "SELECT failure_code, translated_text FROM translation_results WHERE chunk_id='c1'")
    assert row == [{'failure_code': None, 'translated_text': '번역 1'}]
    init_db(db)  # 반복 실행해도 같다
    assert sql(db, 'SELECT COUNT(*) AS n FROM translation_results')[0]['n'] == 2


def test_migration_does_not_touch_existing_rows_or_failures(db):
    repo.save_translation_batch(db, batch([ok(0), fail(1)]))
    before = sql(db, 'SELECT * FROM translation_results ORDER BY chunk_id')
    init_db(db)
    assert sql(db, 'SELECT * FROM translation_results ORDER BY chunk_id') == before
