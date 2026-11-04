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
    translated_a = SegmentInput('c1', 0, 5, 'First', target='translation', translation_revision_id='t1')
    translated_b = SegmentInput('c1', 0, 5, 'First', target='translation', translation_revision_id='t2')
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
