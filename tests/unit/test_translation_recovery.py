"""번역 실패 구간 복구(#30)의 저장·게시·잠금·마이그레이션을 검증한다."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from solo_leveling.domain.models import (
    Chunk, EmbeddingSet, JobStage, JobStatus, Paper, PaperVersion, ParseRevision, ProcessingJob,
    TranslationRecoveryDisposition, TranslationRevision,
)
from solo_leveling.domain.translation import (
    ChunkTranslationResult, TranslationBatchResult, TranslationFailureCode, TranslationRequest,
    TranslationSettings,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db

SETTINGS = TranslationSettings('p', 'm', 'v1')
N = 10
FAILED = (3, 7)


def req(i):
    return TranslationRequest(f'c{i}', 'p1', f'original {i}')


def ok(i, tag='번역'):
    return ChunkTranslationResult(req(i), f'{tag} {i}')


def fail(i):
    return ChunkTranslationResult(req(i), None, TranslationFailureCode.PROVIDER_UNAVAILABLE)


def batch(rev_id, items):
    return TranslationBatchResult(TranslationRevision(rev_id, 'p1', 'p', 'm'), SETTINGS, tuple(items))


def emb(set_id, rev_id):
    return EmbeddingSet(set_id, rev_id, 'model', 4)


def chunk_ids(*excluded):
    return [f'c{i}' for i in range(N) if i not in excluded]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'a.db')
    init_db(path)
    repo.insert_paper(path, Paper('paper'))
    repo.insert_version(path, PaperVersion('v1', 'paper', 'h', 'x.pdf', 'x.pdf'))
    repo.insert_parse_revision(path, ParseRevision('p1', 'v1', 3))
    repo.insert_chunks(path, [Chunk(f'c{i}', 'p1', i, f'original {i}') for i in range(N)])
    repo.insert_job(path, ProcessingJob('j1', 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE))
    repo.save_translation_batch(path, batch('t1', [fail(i) if i in FAILED else ok(i) for i in range(N)]))
    repo.publish_search_index(path, 'v1', 'p1', emb('e1', 't1'), 'j1', chunk_ids(*FAILED), ['partial'])
    return path


def recovery_job(job_id='j2'):
    return ProcessingJob(job_id, 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE)


def run_recovery(db, job_id='j2', rev='t2', items=None):
    repo.claim_translation_recovery(db, recovery_job(job_id), 't1')
    repo.save_recovery_translation(db, job_id, batch(rev, items or [ok(i, '복구') for i in FAILED]))
    repo.reserve_recovery_embedding_set(db, job_id, 'e2')


def rows(db, sql, *args):
    with closing(get_connection(db)) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def test_first_translation_keeps_chunks_text_and_adds_translated_text(db):
    got = rows(db, 'SELECT chunk_id, translated_text FROM translation_results WHERE translation_revision_id=? '
                   'AND failure_code IS NULL ORDER BY chunk_id', 't1')
    assert len(got) == N - len(FAILED) and got[0]['translated_text'] == '번역 0'
    assert rows(db, 'SELECT text FROM chunks WHERE chunk_id=?', 'c0')[0]['text'] == '번역 0'
    assert [c.chunk_id for c in repo.get_translated_chunks(db, 'p1', 't1')] == [
        f'c{i}' for i in range(N) if i not in FAILED]


def test_recovery_creates_new_revision_and_publishes_atomically(db):
    run_recovery(db)
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    assert rows(db, "SELECT * FROM search_index_chunk_snapshots WHERE embedding_set_id='e2'") == [
        {'embedding_set_id': 'e2', 'source': 'publish'}]
    init_db(db)
    assert [r['chunk_id'] for r in rows(
        db, "SELECT chunk_id FROM search_index_chunks WHERE embedding_set_id='e2' ORDER BY chunk_id")] == sorted(
            chunk_ids())
    chunks = repo.get_translated_chunks(db, 'p1', 't2')
    assert len(chunks) == N
    assert {c.chunk_id: c.text for c in chunks}['c3'] == '복구 3'
    assert {c.chunk_id: c.text for c in chunks}['c0'] == '번역 0'
    # 기존 번역·chunks.text는 그대로, 기존 색인은 기본에서 내려간다.
    assert len(repo.get_translated_chunks(db, 'p1', 't1')) == N - len(FAILED)
    assert rows(db, 'SELECT text FROM chunks WHERE chunk_id=?', 'c3')[0]['text'] is None
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e2'
    assert repo.get_search_index(db, 'v1', embedding_set_id='e1')['embedding_set_id'] == 'e1'
    assert [r['is_default'] for r in rows(db, 'SELECT is_default FROM search_indexes ORDER BY embedding_set_id')] == [0, 1]
    assert repo.get_job(db, 'j2').status == JobStatus.READY
    assert rows(db, 'SELECT * FROM translation_recovery_locks') == []


def test_keyed_recovery_replays_status_and_rejects_changed_input(db):
    accepted = repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash-1', 'e1')
    assert accepted.disposition == TranslationRecoveryDisposition.ACCEPTED
    assert accepted.job_id == 'j2'
    repeated = repo.prepare_translation_recovery(db, recovery_job('unused'), 'request-1', 'hash-1', 'e1')
    assert repeated.disposition == TranslationRecoveryDisposition.IN_PROGRESS
    assert repeated.job_id == 'j2'
    conflict = repo.prepare_translation_recovery(db, recovery_job('unused'), 'request-1', 'different', 'e1')
    assert conflict.disposition == TranslationRecoveryDisposition.REQUEST_CONFLICT
    assert conflict.job_id is None


def test_concurrent_same_key_starts_only_one_recovery_job(db):
    def prepare(job_id):
        return repo.prepare_translation_recovery(db, recovery_job(job_id), 'request-1', 'hash-1', 'e1')

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, ('j2', 'j3')))
    assert {result.disposition for result in results} == {
        TranslationRecoveryDisposition.ACCEPTED,
        TranslationRecoveryDisposition.IN_PROGRESS,
    }
    assert {result.job_id for result in results} in ({'j2'}, {'j3'})
    assert len(rows(db, "SELECT job_id FROM processing_jobs WHERE job_id IN ('j2', 'j3')")) == 1
    assert len(rows(db, 'SELECT * FROM translation_recovery_requests')) == 1


def test_keyed_recovery_distinguishes_base_change_and_no_target(db):
    changed = repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash', 'other')
    assert changed.disposition == TranslationRecoveryDisposition.BASE_CHANGED
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE translation_results SET failure_code=NULL, translated_text='복구' "
                     "WHERE translation_revision_id='t1'")
    no_target = repo.prepare_translation_recovery(db, recovery_job(), 'request-2', 'hash', 'e1')
    assert no_target.disposition == TranslationRecoveryDisposition.NO_TARGET
    assert rows(db, 'SELECT * FROM translation_recovery_requests') == []


def test_keyed_recovery_replays_interrupted_without_automatic_restart(db):
    repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash', 'e1')
    assert repo.mark_interrupted_jobs_on_startup(db) == 1
    replay = repo.prepare_translation_recovery(db, recovery_job('j3'), 'request-1', 'hash', 'e1')
    assert replay.disposition == TranslationRecoveryDisposition.INTERRUPTED
    assert replay.job_id == 'j2'
    assert rows(db, "SELECT job_id FROM processing_jobs WHERE job_id='j3'") == []


def test_reserved_embedding_set_limits_publish_and_cleanup_ownership(db):
    repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash', 'e1')
    repo.save_recovery_translation(db, 'j2', batch('t2', [ok(i, '복구') for i in FAILED]))
    repo.reserve_recovery_embedding_set(db, 'j2', 'e2')
    assert repo.get_unpublished_recovery_embedding_set(db, 'j2') == 'e2'
    with pytest.raises(ValueError, match='already reserved'):
        repo.reserve_recovery_embedding_set(db, 'j2', 'other')
    with pytest.raises(ValueError, match='not reserved'):
        repo.publish_recovered_index(db, 'j2', emb('other', 't2'), chunk_ids(), [])
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    assert repo.get_unpublished_recovery_embedding_set(db, 'j2') is None
    completed = repo.prepare_translation_recovery(db, recovery_job('ignored'), 'request-1', 'hash', 'e1')
    assert completed.disposition == TranslationRecoveryDisposition.COMPLETED
    assert completed.new_embedding_set_id == 'e2'


def test_failed_recovery_keeps_only_its_unpublished_cleanup_candidate(db):
    repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash', 'e1')
    repo.reserve_recovery_embedding_set(db, 'j2', 'e2')
    repo.fail_translation_recovery(db, 'j2', ['INDEX_FAILED'])
    assert rows(db, 'SELECT * FROM translation_recovery_locks') == []
    assert repo.get_unpublished_recovery_embedding_set(db, 'j2') == 'e2'
    replay = repo.prepare_translation_recovery(db, recovery_job('j3'), 'request-1', 'hash', 'e1')
    assert replay.disposition == TranslationRecoveryDisposition.FAILED
    assert replay.new_embedding_set_id == 'e2'
    assert repo.get_unpublished_recovery_embedding_set(db, 'unknown') is None


def test_failed_recovery_keeps_embedding_set_ownership_from_later_job(db):
    repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash-1', 'e1')
    repo.reserve_recovery_embedding_set(db, 'j2', 'e2')
    repo.fail_translation_recovery(db, 'j2', ['INDEX_FAILED'])

    accepted = repo.prepare_translation_recovery(db, recovery_job('j3'), 'request-2', 'hash-2', 'e1')
    assert accepted.disposition == TranslationRecoveryDisposition.ACCEPTED
    with pytest.raises(ValueError, match='owned by another job'):
        repo.reserve_recovery_embedding_set(db, 'j3', 'e2')

    assert repo.get_unpublished_recovery_embedding_set(db, 'j2') == 'e2'
    assert repo.get_unpublished_recovery_embedding_set(db, 'j3') is None
    repo.reserve_recovery_embedding_set(db, 'j3', 'e3')
    repo.reserve_recovery_embedding_set(db, 'j3', 'e3')
    assert repo.get_unpublished_recovery_embedding_set(db, 'j3') == 'e3'


def test_publish_rejects_same_translation_revision_with_changed_default_index(db):
    repo.prepare_translation_recovery(db, recovery_job(), 'request-1', 'hash', 'e1')
    repo.save_recovery_translation(db, 'j2', batch('t2', [ok(i, '복구') for i in FAILED]))
    repo.reserve_recovery_embedding_set(db, 'j2', 'e2')
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE search_indexes SET is_default=0 WHERE embedding_set_id='e1'")
        conn.execute("INSERT INTO embedding_sets VALUES ('replacement', 't1', 'model', 4, 'now')")
        conn.execute("INSERT INTO search_indexes (embedding_set_id, version_id, parse_revision_id, "
                     "translation_revision_id, job_id, chunk_count, is_default) "
                     "VALUES ('replacement', 'v1', 'p1', 't1', 'j1', 8, 1)")
    with pytest.raises(ValueError, match='default index changed'):
        repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'replacement'


def test_metadata_for_initial_resume_and_pinned_revision(db):
    run_recovery(db)
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    assert repo.get_translation_metadata(db, 'p1')['translation_revision_id'] == 't1'
    assert repo.get_translation_metadata(db, 'p1', 't2')['translation_revision_id'] == 't2'
    assert repo.get_translation_metadata(db, 'different-parse', 't2') is None


def test_pinned_index_rejects_mismatched_revision(db):
    run_recovery(db)
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    assert repo.get_search_index(db, 'v1', 'p1', 't1') is None  # 기본 색인은 t2
    assert repo.get_search_index(db, 'v1', 'p1', 't1', 'e1')['embedding_set_id'] == 'e1'
    assert repo.get_search_index(db, 'v1', 'p1', 't1', 'e2') is None


def test_partial_recovery_publishes_when_more_chunks_succeed(db):
    run_recovery(db, items=[ok(3, '복구'), fail(7)])
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(7), ['partial'])
    assert repo.get_search_index(db, 'v1')['chunk_count'] == N - 1


def test_recovery_records_exact_ids_when_result_has_null_text(db):
    run_recovery(db)
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE translation_results SET translated_text=NULL WHERE translation_revision_id='t2' "
                     "AND chunk_id='c7'")
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(7), ['partial'])
    assert [r['chunk_id'] for r in rows(
        db, "SELECT chunk_id FROM search_index_chunks WHERE embedding_set_id='e2' ORDER BY chunk_id")] == sorted(
            chunk_ids(7))


def test_recovery_rejects_same_count_different_ids_atomically(db):
    run_recovery(db, items=[ok(3, '복구'), fail(7)])
    wrong_ids = [chunk_id for chunk_id in chunk_ids(7) if chunk_id != 'c9'] + ['c7']
    with pytest.raises(ValueError, match='id set does not match'):
        repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), wrong_ids, ['partial'])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    assert rows(db, "SELECT * FROM embedding_sets WHERE embedding_set_id='e2'") == []
    assert rows(db, "SELECT * FROM search_indexes WHERE embedding_set_id='e2'") == []
    assert rows(db, "SELECT * FROM search_index_chunks WHERE embedding_set_id='e2'") == []


def test_publish_rejected_when_nothing_recovered(db):
    run_recovery(db, items=[fail(3), fail(7)])
    with pytest.raises(ValueError):
        repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(*FAILED), [])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    assert repo.get_job(db, 'j2').status == JobStatus.PROCESSING


def test_publish_rejects_wrong_count_and_leaves_everything_unchanged(db):
    run_recovery(db)
    with pytest.raises(ValueError):
        repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(7), [])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    assert rows(db, 'SELECT * FROM embedding_sets WHERE embedding_set_id=?', 'e2') == []
    assert len(rows(db, 'SELECT * FROM translation_recovery_locks')) == 1


def test_publish_rejects_embedding_set_of_other_revision(db):
    run_recovery(db)
    with pytest.raises(ValueError):
        repo.publish_recovered_index(db, 'j2', emb('e2', 't1'), chunk_ids(), [])


def test_duplicate_recovery_is_rejected_and_lock_is_unique(db):
    repo.claim_translation_recovery(db, recovery_job('j2'), 't1')
    with pytest.raises(ValueError, match='REQUEST_IN_PROGRESS'):
        repo.claim_translation_recovery(db, recovery_job('j3'), 't1')
    assert rows(db, 'SELECT job_id FROM processing_jobs WHERE job_id=?', 'j3') == []
    with closing(get_connection(db)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO translation_recovery_locks (version_id, job_id, base_translation_revision_id, "
                     "created_at) VALUES ('v1', 'jx', 't1', 'now')")


def test_stale_lock_is_cleared_after_job_failed(db):
    repo.claim_translation_recovery(db, recovery_job('j2'), 't1')
    repo.fail_translation_recovery(db, 'j2', ['recovery_failed'])
    assert rows(db, 'SELECT * FROM translation_recovery_locks') == []
    assert repo.get_job(db, 'j2').status == JobStatus.FAILED
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e1'
    repo.claim_translation_recovery(db, recovery_job('j3'), 't1')


def test_stale_lock_row_of_finished_job_is_cleared_on_claim(db):
    repo.claim_translation_recovery(db, recovery_job('j2'), 't1')
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE processing_jobs SET status='failed' WHERE job_id='j2'")
    repo.claim_translation_recovery(db, recovery_job('j3'), 't1')
    assert [r['job_id'] for r in rows(db, 'SELECT job_id FROM translation_recovery_locks')] == ['j3']


def test_claim_requires_default_index_base_with_failures(db):
    with pytest.raises(ValueError):
        repo.claim_translation_recovery(db, recovery_job(), 'nope')
    run_recovery(db)
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    with pytest.raises(ValueError):  # t1은 더 이상 기본 색인이 아니다
        repo.claim_translation_recovery(db, recovery_job('j3'), 't1')
    with pytest.raises(ValueError):  # t2에는 실패가 없다
        repo.claim_translation_recovery(db, recovery_job('j3'), 't2')


def test_recovery_must_cover_exactly_failed_chunks_with_same_settings(db):
    repo.claim_translation_recovery(db, recovery_job(), 't1')
    with pytest.raises(ValueError):
        repo.save_recovery_translation(db, 'j2', batch('t2', [ok(3)]))
    with pytest.raises(ValueError):
        repo.save_recovery_translation(db, 'j2', batch('t2', [ok(3), ok(7), ok(0)]))
    other = TranslationBatchResult(TranslationRevision('t2', 'p1', 'p', 'm'),
                                   TranslationSettings('p', 'm', 'v2'), (ok(3), ok(7)))
    with pytest.raises(ValueError):
        repo.save_recovery_translation(db, 'j2', other)
    assert rows(db, 'SELECT * FROM translation_revisions WHERE translation_revision_id=?', 't2') == []
    repo.save_recovery_translation(db, 'j2', batch('t2', [ok(3), ok(7)]))
    with pytest.raises(ValueError):
        repo.save_recovery_translation(db, 'j2', batch('t3', [ok(3), ok(7)]))


def test_save_and_publish_require_active_recovery(db):
    with pytest.raises(ValueError):
        repo.save_recovery_translation(db, 'j2', batch('t2', [ok(3), ok(7)]))
    with pytest.raises(ValueError):
        repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    with pytest.raises(ValueError):
        repo.fail_translation_recovery(db, 'j2', [])


def test_recovery_source_text_must_match_chunks(db):
    repo.claim_translation_recovery(db, recovery_job(), 't1')
    bad = ChunkTranslationResult(TranslationRequest('c3', 'p1', 'changed'), '번역')
    with pytest.raises(ValueError):
        repo.save_recovery_translation(db, 'j2', batch('t2', [bad, ok(7)]))


def test_one_default_index_per_version_is_enforced_by_database(db):
    with closing(get_connection(db)) as conn:
        conn.execute('INSERT INTO embedding_sets VALUES (?, ?, ?, ?, ?)', ('ex', 't1', 'm', 4, 'now'))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO search_indexes (embedding_set_id, version_id, parse_revision_id, "
                         "translation_revision_id, job_id, chunk_count, is_default) "
                         "VALUES ('ex', 'v1', 'p1', 't1', 'j1', 1, 1)")


def test_backfill_is_repeat_safe_and_never_overwrites(db):
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE translation_results SET translated_text=NULL WHERE chunk_id='c0'")
        conn.execute("UPDATE translation_results SET translated_text='보존' WHERE chunk_id='c1'")
    init_db(db)
    init_db(db)
    got = {r['chunk_id']: r['translated_text'] for r in rows(
        db, 'SELECT chunk_id, translated_text FROM translation_results WHERE translation_revision_id=?', 't1')}
    assert got['c0'] == '번역 0' and got['c1'] == '보존' and got['c3'] is None


OLD_SEARCH_INDEXES = '''
CREATE TABLE search_indexes (
    embedding_set_id TEXT PRIMARY KEY REFERENCES embedding_sets(embedding_set_id),
    version_id TEXT NOT NULL UNIQUE REFERENCES paper_versions(version_id),
    parse_revision_id TEXT NOT NULL REFERENCES parse_revisions(parse_revision_id),
    translation_revision_id TEXT NOT NULL REFERENCES translation_revisions(translation_revision_id),
    job_id TEXT NOT NULL REFERENCES processing_jobs(job_id),
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0)
);
'''


def test_old_shape_database_is_migrated_without_losing_rows(db):
    with closing(get_connection(db)) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('DROP INDEX IF EXISTS idx_search_indexes_default')
        conn.execute('DROP INDEX IF EXISTS idx_search_indexes_version')
        conn.execute('DROP TABLE search_indexes')
        conn.executescript(OLD_SEARCH_INDEXES)
        conn.execute("INSERT INTO search_indexes VALUES ('e1', 'v1', 'p1', 't1', 'j1', 8)")
        conn.commit()
    init_db(db)
    init_db(db)
    assert rows(db, 'SELECT embedding_set_id, is_default, chunk_count FROM search_indexes') == [
        {'embedding_set_id': 'e1', 'is_default': 1, 'chunk_count': 8}]
    assert rows(db, 'PRAGMA foreign_key_check') == []
    run_recovery(db)
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    assert repo.get_search_index(db, 'v1')['embedding_set_id'] == 'e2'


def test_scaled_96_of_98_scenario(tmp_path):
    path = str(tmp_path / 'big.db')
    init_db(path)
    repo.insert_paper(path, Paper('paper'))
    repo.insert_version(path, PaperVersion('v1', 'paper', 'h', 'x.pdf', 'x.pdf'))
    repo.insert_parse_revision(path, ParseRevision('p1', 'v1', 3))
    repo.insert_chunks(path, [Chunk(f'c{i}', 'p1', i, f'original {i}') for i in range(98)])
    repo.insert_job(path, ProcessingJob('j1', 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE))
    repo.save_translation_batch(path, batch('t1', [fail(i) if i in (40, 41) else ok(i) for i in range(98)]))
    repo.publish_search_index(path, 'v1', 'p1', emb('e1', 't1'), 'j1',
                              [f'c{i}' for i in range(98) if i not in (40, 41)], ['partial'])
    repo.claim_translation_recovery(path, recovery_job(), 't1')
    repo.save_recovery_translation(path, 'j2', batch('t2', [ok(40, '복구'), ok(41, '복구')]))
    repo.reserve_recovery_embedding_set(path, 'j2', 'e2')
    repo.publish_recovered_index(path, 'j2', emb('e2', 't2'), [f'c{i}' for i in range(98)], [])
    assert repo.get_search_index(path, 'v1')['chunk_count'] == 98
    assert len(repo.get_translated_chunks(path, 'p1', 't2')) == 98
    assert len(repo.get_translated_chunks(path, 'p1', 't1')) == 96


def test_status_and_registration_after_recovery(db):
    from solo_leveling.workers.ingestion import get_ingestion_status
    repo.update_job_status(db, 'j1', JobStatus.READY)
    run_recovery(db)
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), chunk_ids(), [])
    old = get_ingestion_status(db, 'j1')
    new = get_ingestion_status(db, 'j2')
    assert old['result']['embedding_set_id'] == 'e1' and new['result']['embedding_set_id'] == 'e2'
    from solo_leveling.domain.models import IngestionDisposition
    reg = repo.prepare_ingestion(db, Paper('paper'), PaperVersion('vX', 'paper', 'h', 'x.pdf', 'x.pdf'), 'jnew')
    assert reg.disposition == IngestionDisposition.COMPLETED and reg.job_id == 'j2'
