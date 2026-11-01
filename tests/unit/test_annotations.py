"""메모·하이라이트 저장소(M1-11, #56)를 실제 SQLite로 검증한다: 생성·수정·삭제, 위치 검증, 충돌, 삭제 정책, 마이그레이션."""
import sqlite3
import threading
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.annotation import (
    CHUNK_NOT_IN_CONTEXT, CONTEXT_NOT_FOUND, MAX_BODY_LENGTH, MAX_QUOTE_LENGTH, MAX_SEGMENTS,
    OFFSET_OUT_OF_RANGE, QUOTE_MISMATCH, TRANSLATION_REVISION_MISMATCH, AnnotationOutcome,
    AnnotationValidationError, SegmentInput)
from solo_leveling.domain.models import Chunk, LearningContext, ParseRevision
from solo_leveling.infrastructure.database import annotations as ann
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401  (fixture)

SECRET = '비밀-메모-본문'


@pytest.fixture
def env(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.insert_chunks(db, [Chunk('c3', 'p1', 2, '가🙂abc éx', pdf_page=2)])  # 코드 포인트·결합 문자 확인용
    return db, context.context_id


def seg(chunk='c1', start=0, end=5, quote='First', **kw):
    return SegmentInput(chunk, start, end, quote, **kw)


def counts(db):
    with closing(get_connection(db)) as conn:
        return (conn.execute('SELECT COUNT(*) FROM annotations').fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM annotation_segments').fetchone()[0])


def make(db, context_id, kind='highlight', body='', segments=None, step='preview'):
    segments = [seg()] if segments is None else segments
    result = ann.create_annotation(db, context_id, kind, body, segments, step)
    assert result.outcome is AnnotationOutcome.SAVED
    return result.annotation


# ── 생성 ────────────────────────────────────────────────────
def test_create_highlight_stores_record_and_segment_together(env):
    db, ctx = env
    a = make(db, ctx)
    assert (a.kind, a.revision, a.step, a.context_id) == ('highlight', 1, 'preview', ctx)
    s = a.segments[0]
    assert (s.position, s.chunk_id, s.parse_revision_id, s.target) == (0, 'c1', 'p1', 'original')
    assert (s.start_offset, s.end_offset, s.quote_snapshot) == (0, 5, 'First')
    assert counts(db) == (1, 1) and ann.get_annotation(db, a.annotation_id) == a


def test_create_note_with_and_without_segments(env):
    db, ctx = env
    plain = make(db, ctx, 'note', '전체 메모', segments=[], step=None)
    attached = make(db, ctx, 'note', '구간 메모', segments=[seg(), seg('c2', 0, 6, 'Second')])
    assert plain.segments == () and plain.step is None
    assert [s.position for s in attached.segments] == [0, 1]
    assert counts(db) == (2, 2)


@pytest.mark.parametrize('kind,body,segments,step', [
    ('bogus', 'x', [seg()], None),
    ('note', '   ', [], None),                    # 메모는 본문이 필수
    ('note', 'x' * (MAX_BODY_LENGTH + 1), [], None),
    ('highlight', '', [], None),                  # 하이라이트는 구간이 필수
    ('highlight', '', [seg()] * (MAX_SEGMENTS + 1), None),
    ('note', 'x', [], 'bogus-step'),
    ('note', 5, [], None),
])
def test_invalid_fields_raise_and_save_nothing(env, kind, body, segments, step):
    db, ctx = env
    with pytest.raises(AnnotationValidationError):
        ann.create_annotation(db, ctx, kind, body, segments, step)
    assert counts(db) == (0, 0)


@pytest.mark.parametrize('kw', [
    dict(start=3, end=3, quote=''), dict(start=-1, end=4, quote='irst'), dict(start=4, end=2, quote='ab'),
    dict(start=0, end=5, quote='Fir'),                        # 길이가 범위와 다름
    dict(start=0, end=5, quote='x' * 5, target='bogus'),
    dict(start=True, end=5, quote='irst'), dict(start=0.0, end=5, quote='First'),
    dict(target='translation'),                               # 번역 구간은 번역 리비전 필요
    dict(translation_revision_id='t1'),                       # 원문 구간은 번역 리비전 불가
    dict(start=0, end=MAX_QUOTE_LENGTH + 1, quote='x' * (MAX_QUOTE_LENGTH + 1)),
])
def test_invalid_segment_input_raises(kw):
    args = dict(start=0, end=5, quote='First')
    args.update(kw)
    with pytest.raises(AnnotationValidationError):
        SegmentInput('c1', args.pop('start'), args.pop('end'), args.pop('quote'), **args)


# ── 위치·참조 검증(INVALID_REFERENCE) ───────────────────────
def invalid(db, ctx, segments, context_id=None):
    before = counts(db)
    result = ann.create_annotation(db, context_id or ctx, 'highlight', '', segments)
    assert result.outcome is AnnotationOutcome.INVALID_REFERENCE and result.annotation is None
    assert counts(db) == before  # 일부만 저장되지 않는다
    assert SECRET not in repr(result)
    return result.code


def test_unknown_context_chunk_offset_and_quote_are_rejected_with_codes(env):
    db, ctx = env
    assert invalid(db, ctx, [seg()], context_id='nope') == CONTEXT_NOT_FOUND
    assert invalid(db, ctx, [seg('nope')]) == CHUNK_NOT_IN_CONTEXT
    assert invalid(db, ctx, [seg(start=3, end=10, quote='st.' + 'a' * 4)]) == OFFSET_OUT_OF_RANGE
    assert invalid(db, ctx, [seg(quote='Fiber')]) == QUOTE_MISMATCH


def test_chunk_from_another_parse_revision_is_rejected(env):
    db, ctx = env
    repo.insert_parse_revision(db, ParseRevision('p2', 'v1', 1))
    repo.insert_chunks(db, [Chunk('c9', 'p2', 0, 'Other chunk', pdf_page=1)])
    assert invalid(db, ctx, [seg('c9', 0, 5, 'Other')]) == CHUNK_NOT_IN_CONTEXT


def test_one_bad_segment_rolls_back_the_whole_annotation(env):
    db, ctx = env
    assert invalid(db, ctx, [seg(), seg('c2', 0, 6, 'Seconx')]) == QUOTE_MISMATCH


def test_offsets_are_code_points_and_text_is_not_normalized(env):
    db, ctx = env
    # '가🙂abc éx': 인덱스 0 가, 1 🙂, 2 a, 3 b, 4 c, 5 공백, 6 e, 7 결합 악센트, 8 x
    assert make(db, ctx, segments=[seg('c3', 1, 3, '🙂a')]).segments[0].quote_snapshot == '🙂a'
    assert make(db, ctx, segments=[seg('c3', 7, 8, '́')]).segments[0].start_offset == 7
    assert invalid(db, ctx, [seg('c3', 6, 7, '\u00e9')]) == QUOTE_MISMATCH  # 합성 é로는 'e'와 맞지 않는다
    assert invalid(db, ctx, [seg('c3', 0, 9, '가🙂abc e\u0301y')]) == QUOTE_MISMATCH  # 한 글자만 달라도 거부


def test_translation_segment_needs_the_contexts_translation_revision(env):
    db, ctx = env
    ok = make(db, ctx, segments=[seg('c1', 0, 3, '첫째.', target='translation', translation_revision_id='t1')])
    assert ok.segments[0].target == 'translation' and ok.segments[0].translation_revision_id == 't1'
    assert invalid(db, ctx, [seg('c1', 0, 3, '첫째.', target='translation',
                                 translation_revision_id='other')]) == TRANSLATION_REVISION_MISMATCH


# ── 수정 ────────────────────────────────────────────────────
def test_update_changes_body_and_step_and_bumps_revision_only(env):
    db, ctx = env
    a = make(db, ctx, 'note', '처음', segments=[seg()])
    result = ann.update_annotation(db, a.annotation_id, 1, '고친 본문', 'deep_read')
    assert result.outcome is AnnotationOutcome.SAVED
    b = result.annotation
    assert (b.body, b.step, b.revision) == ('고친 본문', 'deep_read', 2)
    assert b.segments == a.segments and b.context_id == a.context_id and b.created_at == a.created_at
    assert ann.update_annotation(db, a.annotation_id, 2, '또', None).annotation.step is None


def test_update_with_stale_revision_conflicts_and_changes_nothing(env):
    db, ctx = env
    a = make(db, ctx, 'note', '처음', segments=[])
    ann.update_annotation(db, a.annotation_id, 1, '둘째', None)
    result = ann.update_annotation(db, a.annotation_id, 1, '낡은 수정', None)
    assert result.outcome is AnnotationOutcome.CONFLICT and result.annotation is None
    current = ann.get_annotation(db, a.annotation_id)
    assert (current.body, current.revision) == ('둘째', 2)


def test_update_not_found_and_invalid_input(env):
    db, ctx = env
    assert ann.update_annotation(db, 'nope', 1, 'x', None).outcome is AnnotationOutcome.NOT_FOUND
    note = make(db, ctx, 'note', '본문', segments=[])
    for bad in ('  ', 'x' * (MAX_BODY_LENGTH + 1)):
        with pytest.raises(AnnotationValidationError):
            ann.update_annotation(db, note.annotation_id, 1, bad, None)
    with pytest.raises(AnnotationValidationError):
        ann.update_annotation(db, note.annotation_id, 1, '본문', 'bogus-step')
    for bad_revision in (0, -1, True, '1', None):
        with pytest.raises(AnnotationValidationError):
            ann.update_annotation(db, note.annotation_id, bad_revision, '본문', None)
    assert ann.get_annotation(db, note.annotation_id).revision == 1  # 실패한 시도는 revision을 올리지 않는다


def test_highlight_body_may_be_emptied_but_a_note_may_not(env):
    db, ctx = env
    h = make(db, ctx, 'highlight', '메모', segments=[seg()])
    assert ann.update_annotation(db, h.annotation_id, 1, '', None).annotation.body == ''


def test_concurrent_updates_with_same_revision_only_one_wins(env):
    db, ctx = env
    a = make(db, ctx, 'note', '처음', segments=[])
    outcomes, barrier = [], threading.Barrier(6)

    def worker(i):
        barrier.wait()
        outcomes.append(ann.update_annotation(db, a.annotation_id, 1, f'수정 {i}', None).outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(o.value for o in outcomes) == ['conflict'] * 5 + ['saved']
    assert ann.get_annotation(db, a.annotation_id).revision == 2


# ── 삭제 ────────────────────────────────────────────────────
def test_delete_removes_only_the_record_and_its_segments(env):
    db, ctx = env
    keep = make(db, ctx, segments=[seg('c2', 0, 6, 'Second')])
    gone = make(db, ctx, 'note', '지울 메모', segments=[seg(), seg('c2', 0, 6, 'Second')])
    result = ann.delete_annotation(db, gone.annotation_id, 1)
    assert result.outcome is AnnotationOutcome.DELETED
    assert ann.get_annotation(db, gone.annotation_id) is None
    assert ann.get_annotation(db, keep.annotation_id) == keep
    assert counts(db) == (1, 1)
    with closing(get_connection(db)) as conn:  # 논문·맥락·청크는 그대로
        assert conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 3
        assert conn.execute('SELECT COUNT(*) FROM learning_contexts').fetchone()[0] == 1


def test_delete_with_stale_revision_conflicts_and_keeps_everything(env):
    db, ctx = env
    a = make(db, ctx)
    ann.update_annotation(db, a.annotation_id, 1, '수정', None)
    assert ann.delete_annotation(db, a.annotation_id, 1).outcome is AnnotationOutcome.CONFLICT
    assert counts(db) == (1, 1)
    assert ann.delete_annotation(db, a.annotation_id, 2).outcome is AnnotationOutcome.DELETED
    assert ann.delete_annotation(db, a.annotation_id, 2).outcome is AnnotationOutcome.NOT_FOUND


def test_referenced_chunk_and_context_cannot_be_deleted_until_annotation_is_removed(env):
    db, ctx = env
    a = make(db, ctx, segments=[seg('c3', 0, 2, '가🙂')])
    with closing(get_connection(db)) as conn:
        for table, column in (('annotations', 'context_id'), ('annotation_segments', 'chunk_id')):
            fk = [r for r in conn.execute(f'PRAGMA foreign_key_list({table})') if r['from'] == column][0]
            assert fk['on_delete'] == 'RESTRICT'
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute("DELETE FROM chunks WHERE chunk_id='c3'")
    assert ann.get_annotation(db, a.annotation_id) is not None  # 거부됐으니 메모는 그대로
    ann.delete_annotation(db, a.annotation_id, 1)
    with closing(get_connection(db)) as conn, conn:
        conn.execute("DELETE FROM chunks WHERE chunk_id='c3'")  # 메모를 지운 뒤에는 지워진다


# ── 조회 ────────────────────────────────────────────────────
def test_list_is_per_context_ordered_and_filterable(env):
    db, ctx = env
    repo.create_learning_context(db, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=[],
                                                     embedding_set_id='idx'))
    first = make(db, ctx, 'note', 'A', segments=[], step='preview')
    second = make(db, ctx, 'highlight', '', segments=[seg()], step='deep_read')
    other = make(db, 'ctx2', 'note', 'B', segments=[], step='preview')
    assert [a.annotation_id for a in ann.list_annotations(db, ctx)] == [first.annotation_id, second.annotation_id]
    assert [a.annotation_id for a in ann.list_annotations(db, ctx, step='preview')] == [first.annotation_id]
    assert [a.annotation_id for a in ann.list_annotations(db, ctx, kind='highlight')] == [second.annotation_id]
    assert [a.annotation_id for a in ann.list_annotations(db, 'ctx2')] == [other.annotation_id]  # 자동 공유 없음
    with pytest.raises(ResourceNotFoundError):
        ann.list_annotations(db, 'nope')


def test_get_segment_texts_returns_current_text_so_service_can_detect_drift(env):
    db, ctx = env
    a = make(db, ctx, segments=[seg(), seg('c1', 0, 3, '첫째.', target='translation',
                                          translation_revision_id='t1')])
    assert ann.get_segment_texts(db, a.annotation_id) == {0: 'First.', 1: '첫째.'}
    with closing(get_connection(db)) as conn, conn:  # 원문이 바뀌면 현재 텍스트가 달라 보인다
        conn.execute("UPDATE chunks SET original_text='Changed.' WHERE chunk_id='c1'")
    assert ann.get_segment_texts(db, a.annotation_id)[0] == 'Changed.'
    assert ann.get_annotation(db, a.annotation_id).segments[0].quote_snapshot == 'First'  # 스냅샷은 보존
    with closing(get_connection(db)) as conn, conn:  # 청크의 번역 리비전이 달라지면 번역 구간은 None
        conn.execute("UPDATE chunks SET translation_revision_id=NULL WHERE chunk_id='c1'")
    assert ann.get_segment_texts(db, a.annotation_id)[1] is None
    with pytest.raises(ResourceNotFoundError):
        ann.get_segment_texts(db, 'nope')


# ── 마이그레이션·로그 ───────────────────────────────────────
def test_init_db_is_repeatable_and_adds_tables_to_an_older_database(env):
    db, ctx = env
    a = make(db, ctx)
    init_db(db)
    init_db(db)
    assert ann.get_annotation(db, a.annotation_id) == a
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DROP TABLE annotation_segments')
        conn.execute('DROP TABLE annotations')  # 이 기능 이전의 DB를 흉내
    init_db(db)
    assert counts(db) == (0, 0)
    assert make(db, ctx).revision == 1
    with closing(get_connection(db)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 3  # 기존 데이터는 그대로


def test_results_and_errors_never_carry_note_text(env):
    db, ctx = env
    result = ann.create_annotation(db, ctx, 'highlight', SECRET, [seg(quote='Fiber')])
    assert SECRET not in repr(result) and 'Fiber' not in repr(result)
    with pytest.raises(AnnotationValidationError) as error:
        ann.create_annotation(db, ctx, 'note', SECRET * 1000, [])
    assert SECRET not in str(error.value)
