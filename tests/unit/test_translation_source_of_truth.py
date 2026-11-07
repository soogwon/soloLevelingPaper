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
        repo.publish_search_index(db, 'v1', 'p1', emb, 'j1', ['c0', 'c1', 'c2'], [])
    repo.publish_search_index(db, 'v1', 'p1', emb, 'j1', ['c0', 'c1'], ['partial'])
    assert repo.get_search_index(db, 'v1')['chunk_count'] == 2


def test_publish_rejects_same_count_different_id_set_without_partial_rows(db):
    repo.save_translation_batch(db, batch([ok(0), ok(1), fail(2), fail(3)]))
    with pytest.raises(ValueError, match='id set does not match'):
        repo.publish_search_index(
            db, 'v1', 'p1', EmbeddingSet('e1', 't1', 'model', 4), 'j1', ['c0', 'c2'], [])
    assert sql(db, "SELECT * FROM embedding_sets WHERE embedding_set_id='e1'") == []
    assert sql(db, "SELECT * FROM search_indexes WHERE embedding_set_id='e1'") == []
    assert sql(db, "SELECT * FROM search_index_chunks WHERE embedding_set_id='e1'") == []


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


# ── B 리뷰 반영(#171): 게시된 색인 스냅샷 복원과 다른 파싱 리비전 사본 ──────────────────────────
from solo_leveling.domain.models import Chunk as _Chunk  # noqa: E402
from solo_leveling.domain.verification_history import (  # noqa: E402
    HistoryClaim, HistoryOutcome, HistoryReference, VerificationHistoryRecord, chunk_content_hash)
from solo_leveling.infrastructure.database import verification_history as vh  # noqa: E402


@pytest.fixture
def published(db):
    """c0, c1 성공 / c2, c3 실패로 게시된 색인(chunk_count=2)과 학습 맥락."""
    from solo_leveling.domain.models import LearningContext
    repo.save_translation_batch(db, batch([ok(0), ok(1), fail(2), fail(3)]))
    repo.publish_search_index(db, 'v1', 'p1', EmbeddingSet('idx', 't1', 'fake', 2), 'j1',
                              ['c0', 'c1'], ['partial'])
    repo.create_learning_context(db, LearningContext('ctx', 'v1', 'p1', 't1', known_concepts=[], embedding_set_id='idx'))
    return db


def members(db, embedding_set='idx'):
    return sorted(r['chunk_id'] for r in sql(db, 'SELECT chunk_id FROM search_index_chunks WHERE embedding_set_id=?',
                                              embedding_set))


def legacy_state(db, keep):
    """이전 DB를 모사한다: 스냅샷을 keep만 남기고, c1의 번역은 chunks.text에만 남긴다(결과 행 없음)."""
    sql(db, 'DROP TRIGGER IF EXISTS trg_embedding_set_chunks')
    sql(db, 'DELETE FROM search_index_chunks')
    for chunk_id in keep:
        sql(db, 'INSERT INTO search_index_chunks VALUES (?, ?)', 'idx', chunk_id)
    sql(db, "DELETE FROM translation_results WHERE chunk_id='c1'")


def history(*chunk_ids, run='r1'):
    texts = {'c0': 'original 0', 'c1': 'original 1', 'c2': 'original 2', 'c3': 'original 3'}
    refs = tuple(HistoryReference(c, chunk_content_hash(texts[c])) for c in chunk_ids)
    return VerificationHistoryRecord(
        run_id=run, context_id='ctx', version_id='v1', parse_revision_id='p1', translation_revision_id='t1',
        embedding_set_id='idx', model='fake', instruction_version='i1', elapsed_ms=1, level='model_checked',
        failure=None, claims=(HistoryClaim('k1', '주장', 'supported', 'full_support', True, refs),))


def test_null_translation_is_not_published_or_accepted_as_a_reference(db):
    """#155 재현: failure_code와 translated_text가 모두 NULL인 c3를 게시 집합에서 제외한다."""
    from solo_leveling.domain.models import LearningContext

    repo.save_translation_batch(db, batch([ok(0), ok(1), fail(2), fail(3)]))
    sql(db, "UPDATE translation_results SET failure_code=NULL, translated_text=NULL WHERE chunk_id='c3'")
    repo.publish_search_index(
        db, 'v1', 'p1', EmbeddingSet('idx', 't1', 'fake', 2), 'j1', ['c0', 'c1'], ['partial'])
    repo.create_learning_context(
        db, LearningContext('ctx', 'v1', 'p1', 't1', known_concepts=[], embedding_set_id='idx'))

    assert members(db) == ['c0', 'c1']
    assert vh.save_verification_history(db, history('c3')).outcome is HistoryOutcome.REFERENCE_MISMATCH
    for table in ('verification_runs', 'verification_claims', 'verification_claim_refs'):
        assert sql(db, f'SELECT * FROM {table}') == []


@pytest.mark.parametrize('keep', [[], ['c0']])
def test_published_snapshot_is_restored_to_exactly_the_success_chunks(published, keep):
    legacy_state(published, keep)
    for _ in range(2):                                   # 첫 번째·두 번째 초기화 모두
        init_db(published)
        assert members(published) == ['c0', 'c1']
    assert vh.save_verification_history(published, history('c0', 'c1')).outcome is HistoryOutcome.SAVED
    assert vh.save_verification_history(published, history('c2', run='r2')).outcome is HistoryOutcome.REFERENCE_MISMATCH
    assert vh.save_verification_history(published, history('c0', 'c3', run='r3')).outcome is HistoryOutcome.REFERENCE_MISMATCH


def test_snapshot_is_replaced_when_candidate_set_matches_published_count(published):
    # 구버전 트리거가 후보 밖 멤버를 기록했어도 게시 개수와 후보 수가 맞으면 정확한 집합으로 교정한다.
    legacy_state(published, ['c0', 'c2'])
    init_db(published)
    init_db(published)
    assert members(published) == ['c0', 'c1']


def test_snapshot_is_not_replaced_when_the_restoration_basis_does_not_match(published):
    # 게시 당시 개수와 후보 수가 다르면 추가하지 않는다.
    legacy_state(published, [])
    sql(published, 'UPDATE search_indexes SET chunk_count=3')
    init_db(published)
    assert members(published) == []
    # 게시 기록의 번역 리비전이 임베딩 세트와 다르면 추가하지 않는다.
    sql(published, 'UPDATE search_indexes SET chunk_count=2')
    sql(published, "INSERT INTO translation_revisions (translation_revision_id, parse_revision_id, provider, model, "
                   "prompt_version, target_language, created_at) VALUES ('t9', 'p1', 'p', 'm', 'v1', NULL, 'x')")
    sql(published, "UPDATE search_indexes SET translation_revision_id='t9'")
    init_db(published)
    assert members(published) == []


def test_upgrade_removes_legacy_null_translation_member_and_rejects_reference(published):
    # 구버전 트리거는 failure_code만 보고 c3을 멤버로 넣었다.
    sql(published, "UPDATE translation_results SET failure_code=NULL, translated_text=NULL WHERE chunk_id='c3'")
    sql(published, "INSERT INTO search_index_chunks VALUES ('idx', 'c3')")
    assert members(published) == ['c0', 'c1', 'c3']

    for _ in range(2):
        init_db(published)
        assert members(published) == ['c0', 'c1']
    assert vh.save_verification_history(published, history('c3')).outcome is HistoryOutcome.REFERENCE_MISMATCH
    for table in ('verification_runs', 'verification_claims', 'verification_claim_refs'):
        assert sql(published, f'SELECT * FROM {table}') == []
    assert vh.save_verification_history(published, history('c0', 'c1', run='r2')).outcome is HistoryOutcome.SAVED


def test_other_published_indexes_are_not_touched(published):
    # 정상 색인의 행을 다시 쓰면 초기화가 실패하도록 한다.
    for operation in ('INSERT', 'DELETE', 'UPDATE'):
        sql(published, f'''CREATE TRIGGER reject_member_{operation} BEFORE {operation} ON search_index_chunks
                           BEGIN SELECT RAISE(ABORT, '정상 색인의 멤버 변경'); END''')
    init_db(published)
    assert members(published) == ['c0', 'c1']


def other_parse_revision_copy(db):
    """p2 소속 청크에 p1의 번역 리비전 t1과 번역 사본을 넣는다. 각 ID는 존재하므로 외래 키 검사는 통과한다."""
    repo.insert_parse_revision(db, ParseRevision('p2', 'v1', 1))
    repo.insert_chunks(db, [_Chunk('x0', 'p2', 0, 'other original')])
    sql(db, "UPDATE chunks SET text='다른 리비전 사본', translation_revision_id='t1' WHERE chunk_id='x0'")


def test_copy_pointing_to_another_parse_revision_is_not_migrated_but_normal_copy_is(db):
    repo.save_translation_batch(db, batch([ok(0), fail(1)]))
    sql(db, "DELETE FROM translation_results WHERE chunk_id='c0'")      # 정상 사본: c0의 번역은 chunks.text에만 있다
    other_parse_revision_copy(db)
    before_failure = sql(db, "SELECT * FROM translation_results WHERE chunk_id='c1'")
    for _ in range(2):
        init_db(db)
        rows = sql(db, 'SELECT chunk_id, failure_code, translated_text FROM translation_results '
                       'WHERE translation_revision_id=? ORDER BY chunk_id', 't1')
        assert [(r['chunk_id'], r['failure_code']) for r in rows] == [('c0', None), ('c1', 'provider_unavailable')]
        assert rows[0]['translated_text'] == '번역 0'
    assert sql(db, "SELECT * FROM translation_results WHERE chunk_id='c1'") == before_failure   # 기존 결과 보존
    assert sql(db, "SELECT COUNT(*) AS n FROM translation_results WHERE chunk_id='x0'")[0]['n'] == 0


def test_existing_result_row_pointing_to_another_parse_revision_gets_no_text_filled(db):
    repo.save_translation_batch(db, batch([ok(0)]))
    other_parse_revision_copy(db)
    sql(db, "INSERT INTO translation_results (translation_revision_id, chunk_id, failure_code, translated_text) "
            "VALUES ('t1', 'x0', NULL, NULL)")
    init_db(db)
    assert sql(db, "SELECT translated_text FROM translation_results WHERE chunk_id='x0'")[0]['translated_text'] is None


def test_mismatched_copy_never_becomes_a_published_snapshot_member(published):
    other_parse_revision_copy(published)
    legacy_state(published, [])
    for _ in range(2):
        init_db(published)
        assert members(published) == ['c0', 'c1']
    assert 'x0' not in members(published)
