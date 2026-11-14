"""메모·하이라이트 목록 키셋 페이지네이션과 입력 제한(#56)을 실제 SQLite로 검증한다."""
import base64
import json
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.annotation import (
    DEFAULT_PAGE_SIZE, DUPLICATE_SEGMENT, INVALID_CURSOR, INVALID_LIMIT, MAX_PAGE_SIZE, MAX_QUOTE_LENGTH,
    MAX_QUOTE_TOTAL_LENGTH, QUOTE_TOTAL_TOO_LONG, AnnotationOutcome, AnnotationValidationError, SegmentInput,
    decode_cursor, encode_cursor)
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.database import annotations as ann
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401  (fixture)


@pytest.fixture
def env(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.insert_chunks(db, [Chunk('c3', 'p1', 2, 'x' * 9000, pdf_page=2)])
    return db, context.context_id


def seg(chunk='c1', start=0, end=5, quote='First', **kw):
    return SegmentInput(chunk, start, end, quote, **kw)


def add_notes(db, ctx, count, step='preview', kind='note'):
    ids = []
    for i in range(count):
        result = ann.create_annotation(db, ctx, kind, f'메모 {i}', [] if kind == 'note' else [seg()], step)
        assert result.outcome is AnnotationOutcome.SAVED
        ids.append(result.annotation.annotation_id)
    return ids


def code_of(call):
    with pytest.raises(AnnotationValidationError) as info:
        call()
    return info.value.code


def walk(db, ctx, **kw):
    pages, cursor = [], None
    while True:
        page = ann.list_annotation_page(db, ctx, cursor=cursor, **kw)
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            return pages


# ── 페이지 크기 ─────────────────────────────────────────────
def test_default_page_size_is_20_and_max_is_50(env):
    db, ctx = env
    add_notes(db, ctx, 55)
    first = ann.list_annotation_page(db, ctx)
    assert len(first.items) == DEFAULT_PAGE_SIZE == 20 and first.next_cursor
    assert len(ann.list_annotation_page(db, ctx, limit=MAX_PAGE_SIZE).items) == MAX_PAGE_SIZE == 50


@pytest.mark.parametrize('bad', [0, -1, 51, True, 1.5, '20'])
def test_limit_out_of_range_is_rejected_with_fixed_code(env, bad):
    db, ctx = env
    assert code_of(lambda: ann.list_annotation_page(db, ctx, limit=bad)) == INVALID_LIMIT


def test_exact_multiple_has_no_extra_empty_page(env):
    db, ctx = env
    add_notes(db, ctx, 6)
    pages = walk(db, ctx, limit=3)
    assert [len(p.items) for p in pages] == [3, 3] and pages[-1].next_cursor is None


def test_empty_context_returns_one_empty_page(env):
    db, ctx = env
    page = ann.list_annotation_page(db, ctx)
    assert page.items == () and page.next_cursor is None


def test_unknown_context_raises_not_found(env):
    db, _ = env
    with pytest.raises(ResourceNotFoundError):
        ann.list_annotation_page(db, 'missing')


# ── 순서·이어 읽기 ─────────────────────────────────────────
def test_pages_cover_everything_once_in_created_at_then_id_order(env):
    db, ctx = env
    ids = add_notes(db, ctx, 11)
    with closing(get_connection(db)) as conn, conn:  # 같은 시각 동률을 만든다
        conn.execute("UPDATE annotations SET created_at='2026-01-01T00:00:00+00:00' WHERE context_id=?", (ctx,))
    pages = walk(db, ctx, limit=4)
    got = [a.annotation_id for p in pages for a in p.items]
    assert got == sorted(ids) and len(set(got)) == 11


def test_cursor_continues_after_anchor_record_is_deleted(env):
    db, ctx = env
    add_notes(db, ctx, 6)
    first = ann.list_annotation_page(db, ctx, limit=3)
    last = first.items[-1]
    assert ann.delete_annotation(db, ctx, last.annotation_id, last.revision).outcome is AnnotationOutcome.DELETED
    second = ann.list_annotation_page(db, ctx, limit=3, cursor=first.next_cursor)
    assert len(second.items) == 3 and all(a.annotation_id != last.annotation_id for a in second.items)


def test_record_added_behind_the_cursor_appears_only_on_refresh(env):
    db, ctx = env
    add_notes(db, ctx, 4)
    first = ann.list_annotation_page(db, ctx, limit=2)
    new_id = add_notes(db, ctx, 1)[0]
    with closing(get_connection(db)) as conn, conn:  # 이미 지나간 위치(가장 이른 시각)에 새 기록
        conn.execute("UPDATE annotations SET created_at='2000-01-01T00:00:00+00:00' WHERE annotation_id=?", (new_id,))
    rest = [a.annotation_id for a in ann.list_annotation_page(db, ctx, limit=10, cursor=first.next_cursor).items]
    assert new_id not in rest
    refreshed = [a.annotation_id for a in ann.list_annotation_page(db, ctx, limit=10).items]
    assert refreshed[0] == new_id


def test_filters_apply_and_cursor_is_bound_to_them(env):
    db, ctx = env
    add_notes(db, ctx, 3, step='preview')
    add_notes(db, ctx, 3, step='review')
    add_notes(db, ctx, 2, step='preview', kind='highlight')
    page = ann.list_annotation_page(db, ctx, step='preview', kind='note', limit=2)
    assert len(page.items) == 2 and all(a.step == 'preview' and a.kind == 'note' for a in page.items)
    assert page.next_cursor
    assert code_of(lambda: ann.list_annotation_page(db, ctx, step='review', kind='note', cursor=page.next_cursor)) \
        == INVALID_CURSOR
    assert code_of(lambda: ann.list_annotation_page(db, ctx, step='preview', cursor=page.next_cursor)) == INVALID_CURSOR
    assert [len(p.items) for p in walk(db, ctx, step='preview', kind='note', limit=2)] == [2, 1]


def test_cursor_from_another_context_is_rejected_and_leaks_nothing(env):
    db, ctx = env
    add_notes(db, ctx, 3)
    page = ann.list_annotation_page(db, ctx, limit=1)
    # 맥락 확인보다 커서 확인이 먼저라, 다른 맥락 id로는 커서 단계에서 거절된다.
    assert code_of(lambda: ann.list_annotation_page(db, 'other-context', cursor=page.next_cursor)) == INVALID_CURSOR


@pytest.mark.parametrize('bad', ['', 'not-base64!!', 'e30', 'x' * 600, 123, base64.urlsafe_b64encode(b'[]').decode(),
                                 base64.urlsafe_b64encode(json.dumps({'c': 'a'}).encode()).decode()])
def test_malformed_cursor_is_invalid_cursor(env, bad):
    db, ctx = env
    assert code_of(lambda: ann.list_annotation_page(db, ctx, cursor=bad)) == INVALID_CURSOR


def test_cursor_roundtrip_and_tampered_values(env):
    token = encode_cursor('ctx', 'preview', None, '2026-01-01T00:00:00+00:00', 'a1')
    assert decode_cursor(token, 'ctx', 'preview', None) == ('2026-01-01T00:00:00+00:00', 'a1')
    with pytest.raises(AnnotationValidationError):
        decode_cursor(token, 'ctx', None, None)
    broken = base64.urlsafe_b64encode(json.dumps(
        {'c': 'ctx', 's': None, 'k': None, 't': 5, 'i': 'a1'}).encode()).decode()
    assert code_of(lambda: decode_cursor(broken, 'ctx', None, None)) == INVALID_CURSOR


def test_page_reads_records_and_segments_together(env):
    db, ctx = env
    add_notes(db, ctx, 3, kind='highlight')
    page = ann.list_annotation_page(db, ctx, limit=2)
    assert all(len(a.segments) == 1 for a in page.items)


# ── 입력 제한 ──────────────────────────────────────────────
def test_quote_total_over_limit_is_rejected_and_nothing_saved(env):
    db, ctx = env
    pieces = [seg('c3', i * MAX_QUOTE_LENGTH, (i + 1) * MAX_QUOTE_LENGTH, 'x' * MAX_QUOTE_LENGTH) for i in range(4)]
    assert ann.create_annotation(db, ctx, 'highlight', '', pieces).outcome is AnnotationOutcome.SAVED  # 정확히 8000자
    assert sum(len(s.quote_snapshot) for s in pieces) == MAX_QUOTE_TOTAL_LENGTH
    over = pieces + [seg('c3', 8000, 8001, 'x')]
    assert code_of(lambda: ann.create_annotation(db, ctx, 'highlight', '', over)) == QUOTE_TOTAL_TOO_LONG
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM annotations').fetchone()[0] == 1


def test_duplicate_segment_is_rejected_but_overlap_is_allowed(env):
    db, ctx = env
    assert code_of(lambda: ann.create_annotation(db, ctx, 'highlight', '', [seg(), seg()])) == DUPLICATE_SEGMENT
    overlapping = [seg(start=0, end=5, quote='First'), seg(start=0, end=4, quote='Firs'),
                   seg(start=1, end=5, quote='irst'), seg(chunk='c2', start=0, end=6, quote='Second')]
    assert ann.create_annotation(db, ctx, 'highlight', '', overlapping).outcome is AnnotationOutcome.SAVED


def test_duplicate_compares_translation_revision_and_original_as_null(env):
    db, ctx = env
    original = seg()
    translated_a = SegmentInput('c1', 0, 3, '첫째.', target='translation', translation_revision_id='t1')
    translated_b = SegmentInput('c1', 0, 3, '첫째.', target='translation', translation_revision_id='t2')
    # 대상·리비전이 다르면 같은 위치여도 중복이 아니다(저장소는 번역 구간을 계속 지원한다).
    assert ann.create_annotation(db, ctx, 'highlight', '', [original, translated_a]).outcome \
        is AnnotationOutcome.SAVED
    assert code_of(lambda: ann.create_annotation(db, ctx, 'highlight', '', [translated_a, translated_a])) \
        == DUPLICATE_SEGMENT
    # 번역 리비전이 맥락과 다르면 기존 규칙대로 잘못된 참조(중복 검사보다 늦게, 저장소에서)
    mismatch = ann.create_annotation(db, ctx, 'highlight', '', [translated_b])
    assert mismatch.outcome is AnnotationOutcome.INVALID_REFERENCE


def test_duplicate_is_checked_per_record_not_across_records(env):
    db, ctx = env
    assert ann.create_annotation(db, ctx, 'highlight', '', [seg()]).outcome is AnnotationOutcome.SAVED
    assert ann.create_annotation(db, ctx, 'highlight', '', [seg()]).outcome is AnnotationOutcome.SAVED


def test_existing_limits_still_apply(env):
    db, ctx = env
    assert code_of(lambda: ann.create_annotation(db, ctx, 'note', 'x' * 5001)) is None  # 본문 5,000자
    with pytest.raises(AnnotationValidationError):
        ann.create_annotation(db, ctx, 'highlight', '', [seg(start=i, end=i + 1, quote='F') for i in range(21)])


# ── B 리뷰 반영: 커서 엄격 검사 + 뒤쪽 페이지 조회 비용 ─────────────────────────────────────────
def _b64(text_or_bytes):
    raw = text_or_bytes if isinstance(text_or_bytes, bytes) else text_or_bytes.encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _crafted(created_at, annotation_id, ensure_ascii=True):
    return _b64(json.dumps({'c': 'ctx', 's': None, 'k': None, 't': created_at, 'i': annotation_id},
                           separators=(',', ':'), sort_keys=True, ensure_ascii=ensure_ascii))


def test_valid_cursor_with_trailing_junk_is_rejected_not_ignored(env):
    db, ctx = env
    add_notes(db, ctx, 6)
    page = ann.list_annotation_page(db, ctx, limit=3)
    good = page.next_cursor
    assert ann.list_annotation_page(db, ctx, limit=3, cursor=good).items            # 정상 커서는 통과
    for junk in ('!!!!', '====', ' ', '\n', '%41', '+', '/', '.', '*'):
        for bad in (good + junk, junk + good, good[:5] + junk + good[5:]):
            error = None
            try:
                ann.list_annotation_page(db, ctx, limit=3, cursor=bad)
            except AnnotationValidationError as caught:
                error = caught
            assert error is not None and error.code == INVALID_CURSOR, repr(bad)
            assert bad not in str(error) and 'ctx' not in str(error)               # 입력을 노출하지 않는다


def test_padded_or_noncanonical_base64_cursor_is_rejected(env):
    db, ctx = env
    add_notes(db, ctx, 4)
    good = ann.list_annotation_page(db, ctx, limit=2).next_cursor
    padded = good + '=' * (-len(good) % 4)
    if padded != good:
        assert code_of(lambda: ann.list_annotation_page(db, ctx, cursor=padded)) == INVALID_CURSOR
    # 끝 문자의 남는 비트만 바꾼 변형은 같은 값으로 디코딩되지만 서버가 발급한 형식이 아니다.
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    if len(good) % 4 in (2, 3):
        index = alphabet.index(good[-1])
        for other in (alphabet[(index + 1) % 64], alphabet[(index + 2) % 64], alphabet[(index + 3) % 64]):
            variant = good[:-1] + other
            if variant != good and base64.urlsafe_b64decode(variant + '=' * (-len(variant) % 4)) == \
                    base64.urlsafe_b64decode(good + '=' * (-len(good) % 4)):
                assert code_of(lambda: ann.list_annotation_page(db, ctx, cursor=variant)) == INVALID_CURSOR


@pytest.mark.parametrize('created_at, annotation_id', [
    ('\ud800', 'a1'), ('2026-01-01T00:00:00+00:00', '\ud800'), ('\udfff', '\ud800'), ('2026\udc00', 'a\ud800b')])
def test_lone_surrogate_position_is_invalid_cursor_not_unicode_error(env, created_at, annotation_id):
    db, ctx = env
    add_notes(db, ctx, 2)
    cursor = _crafted(created_at, annotation_id)
    try:
        ann.list_annotation_page(db, ctx, cursor=cursor)
    except AnnotationValidationError as error:
        assert error.code == INVALID_CURSOR and cursor not in str(error) and '\ud800' not in str(error)
    else:
        pytest.fail('INVALID_CURSOR가 아니라 통과했다')


def test_normal_non_ascii_position_still_works(env):
    db, ctx = env
    cursor = _crafted('2026-01-01T00:00:00+00:00', '메모-가', ensure_ascii=False)
    assert decode_cursor(cursor, 'ctx', None, None) == ('2026-01-01T00:00:00+00:00', '메모-가')


def bulk_annotations(db, ctx, total, per_second=1, *, notes_only=False):
    with closing(get_connection(db)) as conn, conn:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.executemany(
            'INSERT INTO annotations(annotation_id, context_id, kind, body, step, revision, created_at, updated_at) '
            "VALUES (?, ?, ?, '', ?, 1, ?, ?)",
            [(f'b{i:05d}', ctx, 'note' if notes_only else ('note', 'highlight')[i % 2], ('preview', 'review', None)[i % 3],
              f'2026-01-01T{(i // per_second) // 3600:02d}:{((i // per_second) % 3600) // 60:02d}:{(i // per_second) % 60:02d}+00:00',
              '2026-01-01T00:00:00+00:00') for i in range(total)])


def page_work(db, ctx, step, kind, after_id, size=20):
    """실제 조회 SQL(annotation_page_query)의 실행 계획과 SQLite 실행 명령 수를 잰다."""
    with closing(get_connection(db)) as conn:
        row = conn.execute('SELECT created_at, annotation_id FROM annotations WHERE annotation_id=?',
                           (after_id,)).fetchone()
        sql, params = ann.annotation_page_query(ctx, step, kind, size, (row['created_at'], row['annotation_id']))
        plan = ' | '.join(r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + sql, params))
        count = [0]
        conn.set_progress_handler(lambda: count.__setitem__(0, count[0] + 1), 1)
        rows = conn.execute(sql, params).fetchall()
        conn.set_progress_handler(None, 0)
    return plan, count[0], len(rows)


@pytest.mark.parametrize('step, kind', [(None, None), ('review', None), (None, 'note'), ('review', 'note')])
def test_deep_page_starts_at_the_cursor_in_the_index(env, step, kind):
    db, ctx = env
    bulk_annotations(db, ctx, 6000)
    plan_early, work_early, n_early = page_work(db, ctx, step, kind, 'b00010')
    plan_deep, work_deep, n_deep = page_work(db, ctx, step, kind, 'b05400')
    for plan in (plan_early, plan_deep):
        assert 'SEARCH' in plan and 'idx_annotations_page' in plan and 'TEMP B-TREE' not in plan, plan
    assert n_early == 21 and n_deep >= 1
    assert work_deep < work_early * 3 and work_deep < 3000, (work_early, work_deep)   # 위치와 거의 무관하다


@pytest.mark.parametrize('step, kind', [(None, None), ('review', 'note')])
def test_deep_cursor_inside_one_timestamp_has_bounded_work(env, step, kind):
    db, ctx = env
    bulk_annotations(db, ctx, 10_000, per_second=10_000, notes_only=True)
    _, early, early_rows = page_work(db, ctx, step, kind, 'b00010')
    _, deep, deep_rows = page_work(db, ctx, step, kind, 'b09001')
    assert early_rows == deep_rows == 21  # 요청 20건과 다음 페이지 확인용 1건
    assert deep < 3 * early, (early, deep)


def test_same_timestamp_boundaries_have_no_gap_or_duplicate(env):
    db, ctx = env
    bulk_annotations(db, ctx, 80, per_second=8)           # 한 시각에 8건씩 몰린다
    expected = [f'b{i:05d}' for i in range(80)]
    for limit in (1, 3, 8, 9, 50):
        seen = [a.annotation_id for page in walk(db, ctx, limit=limit) for a in page.items]
        assert seen == expected, limit
    seen = [a.annotation_id for page in walk(db, ctx, limit=5, step='review', kind='note') for a in page.items]
    assert seen == [f'b{i:05d}' for i in range(80) if i % 3 == 1 and i % 2 == 0]


def test_deep_pages_match_a_full_listing_and_survive_anchor_deletion(env):
    db, ctx = env
    bulk_annotations(db, ctx, 400, per_second=3)
    expected = [f'b{i:05d}' for i in range(400)]
    assert [a.annotation_id for page in walk(db, ctx, limit=50) for a in page.items] == expected
    first = ann.list_annotation_page(db, ctx, limit=50)
    anchor = first.items[-1]
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DELETE FROM annotations WHERE annotation_id=?', (anchor.annotation_id,))   # 기준 기록 삭제
    rest = [a.annotation_id for page in walk_from(db, ctx, first.next_cursor, 50) for a in page.items]
    assert rest == expected[50:]                                  # 삭제된 기준 뒤에서 그대로 이어진다


def walk_from(db, ctx, cursor, limit):
    pages = []
    while cursor is not None:
        page = ann.list_annotation_page(db, ctx, limit=limit, cursor=cursor)
        pages.append(page)
        cursor = page.next_cursor
    return pages
