"""의미 검증 이력 저장소(#83)를 실제 SQLite로 검증한다: 저장·실패 실행·전부 제외, 참조 검증, 롤백, 삭제 범위, 마이그레이션."""
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from solo_leveling.domain.models import Chunk, LearningContext, ParseRevision
from solo_leveling.domain.verification_history import (
    HistoryClaim, HistoryOutcome, HistoryReference, HistoryValidationError, VerificationHistoryRecord,
    chunk_content_hash)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database import verification_history as vh
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401  (fixture)

SECRET = '비밀-주장-본문'


def ref(chunk_id='c1', text='First.'):
    return HistoryReference(chunk_id, chunk_content_hash(text))


def claim(claim_id='k1', verdict='supported', reason='full_support', retained=True, refs=None, text='주장 문구'):
    return HistoryClaim(claim_id, text, verdict, reason, retained, tuple((ref(),) if refs is None else refs))


def record(run_id='r1', claims=None, failure=None, level='model_checked', **kw):
    claims = (claim(),) if claims is None else tuple(claims)
    base = dict(run_id=run_id, context_id='ctx', version_id='v1', parse_revision_id='p1',
                translation_revision_id='t1', embedding_set_id='idx', model='fake', instruction_version='i1',
                elapsed_ms=12, level=level, failure=failure, claims=claims)
    base.update(kw)
    return VerificationHistoryRecord(**base)


@pytest.fixture
def env(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    return db


def counts(db):
    with closing(get_connection(db)) as conn:
        return tuple(conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                     for t in ('verification_runs', 'verification_claims', 'verification_claim_refs'))


# ── 저장·조회 ───────────────────────────────────────────────
def test_save_stores_run_claims_and_references_together_and_reads_back(env):
    rec = record(claims=[claim('k1', refs=[ref('c1', 'First.'), ref('c2', 'Second.')]),
                         claim('k2', 'partially_supported', 'partial_support', False, [ref('c2', 'Second.')])])
    result = vh.save_verification_history(env, rec)
    assert (result.outcome, result.run_id) == (HistoryOutcome.SAVED, 'r1')
    assert counts(env) == (1, 2, 3)
    loaded = vh.get_verification_history(env, 'ctx', 'r1')
    assert replace(loaded, created_at=None) == rec and loaded.created_at
    assert [c.retained for c in loaded.claims] == [True, False]  # verdict와 retained는 따로 보존된다


def test_verdict_and_retained_are_independent(env):
    vh.save_verification_history(env, record(claims=[claim('k1', 'contradicted', 'contradiction', True)]))
    saved = vh.get_verification_history(env, 'ctx', 'r1').claims[0]
    assert (saved.verdict, saved.retained) == ('contradicted', True)


def test_failed_run_keeps_the_run_with_zero_claim_rows(env):
    result = vh.save_verification_history(env, record(claims=[], failure='timeout', level='structural_only'))
    assert result.outcome is HistoryOutcome.SAVED and counts(env) == (1, 0, 0)
    saved = vh.get_verification_history(env, 'ctx', 'r1')
    assert (saved.failure, saved.level, saved.claims) == ('timeout', 'structural_only', ())


def test_failed_run_with_model_checked_level_is_rejected(env):
    with pytest.raises(HistoryValidationError):
        vh.save_verification_history(env, record(claims=[], failure='timeout', level='model_checked'))
    assert counts(env) == (0, 0, 0)


def test_chunk_added_after_publication_is_rejected_and_nothing_is_saved(env):
    with closing(get_connection(env)) as conn, conn:
        conn.execute("INSERT INTO chunks (chunk_id, parse_revision_id, chunk_index, original_text, text, "
                     "translation_revision_id, pdf_page) VALUES ('c3', 'p1', 2, 'Third.', '셋째.', 't1', 2)")
    for refs in ([ref('c3', 'Third.')], [ref('c1', 'First.'), ref('c3', 'Third.')]):
        assert vh.save_verification_history(env, record(claims=[claim(refs=refs)])).outcome \
            is HistoryOutcome.REFERENCE_MISMATCH
    with closing(get_connection(env)) as conn, conn:  # 번역 결과 행까지 만들어도 게시 색인의 청크 수와 달라 거부한다
        conn.execute("INSERT INTO translation_results (translation_revision_id, chunk_id, failure_code) "
                     "VALUES ('t1', 'c3', NULL)")
    assert vh.save_verification_history(env, record(claims=[claim(refs=[ref('c3', 'Third.')])])).outcome \
        is HistoryOutcome.REFERENCE_MISMATCH
    assert counts(env) == (0, 0, 0)


def test_all_claims_excluded_still_keeps_every_real_judgment(env):
    claims = [claim('k1', 'contradicted', 'contradiction', False), claim('k2', 'undetermined',
              'insufficient_support', False, [ref('c2', 'Second.')])]
    assert vh.save_verification_history(env, record(claims=claims)).outcome is HistoryOutcome.SAVED
    saved = vh.get_verification_history(env, 'ctx', 'r1')
    assert [(c.verdict, c.retained) for c in saved.claims] == [('contradicted', False), ('undetermined', False)]


def test_same_run_id_is_not_stored_twice(env):
    vh.save_verification_history(env, record())
    again = vh.save_verification_history(env, record(claims=[claim('other', text='다른 문구')]))
    assert (again.outcome, again.run_id) == (HistoryOutcome.DUPLICATE_RUN, 'r1')
    assert counts(env) == (1, 1, 1)
    assert vh.get_verification_history(env, 'ctx', 'r1').claims[0].claim_id == 'k1'


def test_get_and_list_are_scoped_to_the_context(env):
    repo.create_learning_context(env, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=[],
                                                      embedding_set_id='idx'))
    vh.save_verification_history(env, record('r1'))
    vh.save_verification_history(env, record('r2', context_id='ctx2'))
    assert vh.get_verification_history(env, 'ctx2', 'r1') is None  # 다른 맥락의 실행은 없는 것과 같다
    assert [r.run_id for r in vh.list_verification_history(env, 'ctx')] == ['r1']
    assert [r.run_id for r in vh.list_verification_history(env, 'ctx2')] == ['r2']


# ── 참조 검증 ───────────────────────────────────────────────
def test_unknown_context_or_mismatched_revisions_are_rejected_and_save_nothing(env):
    for change in ({'context_id': 'nope'}, {'version_id': 'v9'}, {'parse_revision_id': 'p9'},
                   {'translation_revision_id': 't9'}, {'embedding_set_id': 'idx9'}):
        assert vh.save_verification_history(env, record(**change)).outcome is HistoryOutcome.REFERENCE_MISMATCH
    assert counts(env) == (0, 0, 0)


def test_chunk_from_another_revision_or_without_translation_is_rejected(env):
    repo.insert_parse_revision(env, ParseRevision('p2', 'v1', 1))
    repo.insert_chunks(env, [Chunk('x1', 'p2', 0, 'Other revision.', pdf_page=1),
                             Chunk('u1', 'p1', 2, 'Untranslated.', pdf_page=2)])  # 번역·색인 범위 밖
    for chunk_id, text in (('x1', 'Other revision.'), ('u1', 'Untranslated.'), ('missing', 'x')):
        result = vh.save_verification_history(env, record(claims=[claim(refs=[ref(chunk_id, text)])]))
        assert result.outcome is HistoryOutcome.REFERENCE_MISMATCH
    assert counts(env) == (0, 0, 0)


def test_content_hash_must_match_the_current_chunk_text(env):
    wrong = record(claims=[claim(refs=[HistoryReference('c1', chunk_content_hash('Different.'))])])
    assert vh.save_verification_history(env, wrong).outcome is HistoryOutcome.REFERENCE_MISMATCH
    with closing(get_connection(env)) as conn, conn:  # 검사 뒤 원문이 바뀌면 옛 해시는 맞지 않는다
        conn.execute("UPDATE chunks SET original_text='Changed.' WHERE chunk_id='c1'")
    assert vh.save_verification_history(env, record()).outcome is HistoryOutcome.REFERENCE_MISMATCH
    assert counts(env) == (0, 0, 0)


def test_one_bad_reference_rejects_the_whole_run(env):
    claims = [claim('k1'), claim('k2', refs=[ref('c2', 'Second.'), HistoryReference('c1', 'f' * 64)])]
    assert vh.save_verification_history(env, record(claims=claims)).outcome is HistoryOutcome.REFERENCE_MISMATCH
    assert counts(env) == (0, 0, 0)


def test_hash_is_exact_utf8_sha256_without_normalization():
    import hashlib
    assert chunk_content_hash('가 A\n') == hashlib.sha256('가 A\n'.encode('utf-8')).hexdigest()
    assert chunk_content_hash('A') != chunk_content_hash('Ａ') != chunk_content_hash('A ')


# ── 롤백 ────────────────────────────────────────────────────
def test_database_error_midway_rolls_back_everything_and_reports_a_code_only(env):
    with closing(get_connection(env)) as conn, conn:  # 두 번째 참조를 넣을 때 실패하도록 만든다
        conn.execute("CREATE TRIGGER fail_ref BEFORE INSERT ON verification_claim_refs "
                     "WHEN NEW.chunk_id='c2' BEGIN SELECT RAISE(ABORT, 'boom'); END")
    rec = record(claims=[claim('k1', refs=[ref('c1', 'First.'), ref('c2', 'Second.')], text=SECRET)])
    result = vh.save_verification_history(env, rec)
    assert result.outcome is HistoryOutcome.SAVE_FAILED and result.run_id is None
    assert SECRET not in repr(result)
    assert counts(env) == (0, 0, 0)  # 실행 행도, 첫 참조도 남지 않는다
    with closing(get_connection(env)) as conn, conn:
        conn.execute('DROP TRIGGER fail_ref')
    assert vh.save_verification_history(env, rec).outcome is HistoryOutcome.SAVED  # 같은 run_id로 다시 저장할 수 있다


# ── 입력 형식 ───────────────────────────────────────────────
@pytest.mark.parametrize('kw', [
    {'run_id': ''}, {'model': ' '}, {'elapsed_ms': -1}, {'elapsed_ms': True}, {'level': 'other'},
    {'failure': 'unknown'}, {'claims': ()},                       # 마친 실행은 주장 판정이 있어야 한다
    {'failure': 'timeout'},                                       # 실패한 실행에는 주장이 없어야 한다
    {'claims': (claim('k1'), claim('k1'))},
    {'claims': (claim(verdict='bogus'),)}, {'claims': (claim(reason='bogus'),)},
    {'claims': (claim(verdict='supported', reason='contradiction'),)},  # 판정과 사유가 안 맞는다
    {'claims': (claim(text=''),)}, {'claims': (claim(text='x' * 4001),)},
    {'claims': (claim(retained=1),)}, {'claims': (claim(refs=[]),)},
    {'claims': (claim(refs=[ref(), ref()]),)},                    # 한 주장 안에서 같은 청크 중복
    {'claims': (claim(refs=[HistoryReference('c1', 'ABC')]),)},
])
def test_invalid_input_raises_and_saves_nothing(env, kw):
    with pytest.raises(HistoryValidationError):
        vh.save_verification_history(env, record(**kw))
    assert counts(env) == (0, 0, 0)


# ── 삭제 범위·마이그레이션 ──────────────────────────────────
def test_chunks_cannot_be_deleted_while_history_references_them(env):
    vh.save_verification_history(env, record())
    with closing(get_connection(env)) as conn, pytest.raises(sqlite3.IntegrityError), conn:
        conn.execute("DELETE FROM chunks WHERE chunk_id='c1'")
    assert counts(env) == (1, 1, 1)


def test_deleting_a_context_cascades_only_its_own_history(env):
    repo.create_learning_context(env, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=[],
                                                      embedding_set_id='idx'))
    vh.save_verification_history(env, record('r1'))
    vh.save_verification_history(env, record('r2', context_id='ctx2'))
    with closing(get_connection(env)) as conn, conn:
        conn.execute("DELETE FROM learning_contexts WHERE context_id='ctx'")
    assert counts(env) == (1, 1, 1)
    assert vh.get_verification_history(env, 'ctx', 'r1') is None
    assert vh.get_verification_history(env, 'ctx2', 'r2') is not None


def test_delete_context_history_removes_only_that_contexts_runs_in_order(env):
    repo.create_learning_context(env, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=[],
                                                      embedding_set_id='idx'))
    vh.save_verification_history(env, record('r1'))
    vh.save_verification_history(env, record('r2', context_id='ctx2'))
    assert vh.delete_context_verification_history(env, 'ctx') == 1
    assert counts(env) == (1, 1, 1)  # 실행·주장·참조가 함께 지워지고 다른 맥락의 것은 남는다
    assert vh.get_verification_history(env, 'ctx2', 'r2') is not None
    with closing(get_connection(env)) as conn, conn:  # 이력을 지운 뒤에도 맥락 삭제가 정상이다
        conn.execute("DELETE FROM learning_contexts WHERE context_id='ctx'")


def test_init_db_is_repeatable_and_adds_tables_to_an_older_database(env):
    vh.save_verification_history(env, record())
    init_db(env)
    init_db(env)  # 반복 실행해도 기존 이력을 건드리지 않는다
    assert counts(env) == (1, 1, 1)
    with closing(get_connection(env)) as conn, conn:  # 이 기능 이전의 DB를 흉내
        for table in ('verification_claim_refs', 'verification_claims', 'verification_runs'):
            conn.execute(f'DROP TABLE {table}')
    init_db(env)
    assert counts(env) == (0, 0, 0)
    assert vh.save_verification_history(env, record()).outcome is HistoryOutcome.SAVED
    with closing(get_connection(env)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_results_and_errors_never_carry_claim_text(env):
    results = [vh.save_verification_history(env, record(claims=[claim(text=SECRET)])),
               vh.save_verification_history(env, record(claims=[claim(text=SECRET)])),
               vh.save_verification_history(env, record('r2', context_id='nope', claims=[claim(text=SECRET)]))]
    assert all(SECRET not in repr(r) for r in results)
    with pytest.raises(HistoryValidationError) as error:
        vh.save_verification_history(env, record('r3', claims=[claim(text=SECRET, refs=[])]))
    assert SECRET not in str(error.value)


def test_same_count_replacement_cannot_make_an_unpublished_chunk_evidence(env):
    # 게시 뒤 c3와 성공 번역을 추가하고 c2의 번역을 실패로 바꿔도 성공 청크 수는 2개로 같다.
    with closing(get_connection(env)) as conn, conn:
        conn.execute("INSERT INTO chunks (chunk_id, parse_revision_id, chunk_index, original_text, text, "
                     "translation_revision_id, pdf_page) VALUES ('c3', 'p1', 2, 'Third.', '셋째.', 't1', 2)")
        conn.execute("INSERT INTO translation_results (translation_revision_id, chunk_id, failure_code) "
                     "VALUES ('t1', 'c3', NULL)")
        conn.execute("UPDATE translation_results SET failure_code='provider_unavailable' "
                     "WHERE translation_revision_id='t1' AND chunk_id='c2'")
        conn.execute("UPDATE chunks SET text=NULL WHERE chunk_id='c2'")
    result = vh.save_verification_history(env, record(claims=[claim(refs=[ref('c3', 'Third.')])]))
    assert result.outcome is HistoryOutcome.REFERENCE_MISMATCH
    assert counts(env) == (0, 0, 0)
    # 게시 당시 집합에 있던 c1은 여전히 근거로 저장할 수 있다.
    assert vh.save_verification_history(env, record('r2')).outcome is HistoryOutcome.SAVED


def test_published_set_is_recorded_at_publication_and_backfilled_for_old_databases(env):
    def members():
        with closing(get_connection(env)) as conn:
            return sorted(r[0] for r in conn.execute(
                "SELECT chunk_id FROM search_index_chunks WHERE embedding_set_id='idx'"))
    assert members() == ['c1', 'c2']
    with closing(get_connection(env)) as conn, conn:  # 이 기능 이전의 DB를 흉내
        conn.execute('DROP TRIGGER trg_embedding_set_chunks')
        conn.execute('DELETE FROM search_index_chunks')
    init_db(env)
    init_db(env)
    assert members() == ['c1', 'c2']


def test_chunk_recovered_by_translation_recovery_can_be_cited_only_after_the_new_index(tmp_path):
    from solo_leveling.domain.models import (EmbeddingSet, JobStage, JobStatus, Paper, PaperVersion,
                                             ProcessingJob, TranslationRevision)
    from solo_leveling.domain.translation import (ChunkTranslationResult, TranslationBatchResult,
                                                  TranslationFailureCode, TranslationRequest, TranslationSettings)
    db = str(tmp_path / 'r.db')
    init_db(db)
    repo.insert_paper(db, Paper('paper'))
    repo.insert_version(db, PaperVersion('v1', 'paper', 'h', 'x.pdf', 'x.pdf'))
    repo.insert_parse_revision(db, ParseRevision('p1', 'v1', 1))
    repo.insert_chunks(db, [Chunk('c1', 'p1', 0, 'First.'), Chunk('c2', 'p1', 1, 'Second.')])
    settings = TranslationSettings('p', 'm', 'v1')

    def batch(rev, items):
        return TranslationBatchResult(TranslationRevision(rev, 'p1', 'p', 'm'), settings, tuple(items))

    def req(chunk_id, text):
        return TranslationRequest(chunk_id, 'p1', text)
    repo.insert_job(db, ProcessingJob('j1', 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE))
    repo.save_translation_batch(db, batch('t1', [
        ChunkTranslationResult(req('c1', 'First.'), '첫째.'),
        ChunkTranslationResult(req('c2', 'Second.'), None, TranslationFailureCode.PROVIDER_UNAVAILABLE)]))
    repo.publish_search_index(db, 'v1', 'p1', EmbeddingSet('e1', 't1', 'm', 2), 'j1', 1, [])
    repo.create_learning_context(db, LearningContext('old', 'v1', 'p1', 't1', known_concepts=[], embedding_set_id='e1'))
    old = record(context_id='old', embedding_set_id='e1', claims=[claim(refs=[ref('c2', 'Second.')])])
    assert vh.save_verification_history(db, old).outcome is HistoryOutcome.REFERENCE_MISMATCH  # 게시되지 않은 청크
    repo.claim_translation_recovery(db, ProcessingJob('j2', 'v1', JobStatus.PROCESSING, JobStage.TRANSLATE), 't1')
    repo.save_recovery_translation(db, 'j2', batch('t2', [ChunkTranslationResult(req('c2', 'Second.'), '둘째.')]))
    repo.publish_recovered_index(db, 'j2', EmbeddingSet('e2', 't2', 'm', 2), 2, [])
    repo.create_learning_context(db, LearningContext('new', 'v1', 'p1', 't2', known_concepts=[], embedding_set_id='e2'))
    new = record('r2', context_id='new', translation_revision_id='t2', embedding_set_id='e2',
                 claims=[claim(refs=[ref('c1', 'First.'), ref('c2', 'Second.')])])
    assert vh.save_verification_history(db, new).outcome is HistoryOutcome.SAVED
    assert vh.save_verification_history(db, replace(old, run_id='r3')).outcome is HistoryOutcome.REFERENCE_MISMATCH
