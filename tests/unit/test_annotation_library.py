"""#206 전체 메모·하이라이트 읽기 계약."""
from contextlib import closing

import pytest

from solo_leveling.domain.annotation import AnnotationValidationError, INVALID_CURSOR, SegmentInput
from solo_leveling.infrastructure.database import annotation_library as library
from solo_leveling.infrastructure.database import annotations as annotations
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_annotations import env  # noqa: F401
from tests.unit.test_learning_persistence import setup_db  # noqa: F401


def note(db, context_id, body, *, segment=None):
    result = annotations.create_annotation(
        db, context_id, 'note', body, [] if segment is None else [segment])
    return result.annotation


def at(db, annotation_id, timestamp):
    with closing(get_connection(db)) as conn, conn:
        conn.execute('UPDATE annotations SET created_at=?, updated_at=? WHERE annotation_id=?',
                     (timestamp, timestamp, annotation_id))


def code_of(call):
    with pytest.raises(AnnotationValidationError) as raised:
        call()
    return raised.value.code


def test_lists_all_contexts_newest_first_with_document_metadata_and_stable_ties(env):
    db, ctx = env
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE papers SET title='Attention Paper' WHERE paper_id='paper'")
        conn.execute("INSERT INTO learning_contexts SELECT 'ctx2', version_id, parse_revision_id, "
                     "translation_revision_id, embedding_set_id, goal, known_concepts, created_at "
                     "FROM learning_contexts WHERE context_id=?", (ctx,))
    first = note(db, ctx, 'older')
    second = note(db, 'ctx2', 'newer')
    tied = note(db, ctx, 'tie')
    at(db, first.annotation_id, '2026-01-01T00:00:00+00:00')
    at(db, second.annotation_id, '2026-01-02T00:00:00+00:00')
    at(db, tied.annotation_id, '2026-01-02T00:00:00+00:00')

    page = library.list_annotation_library(db)
    assert [x.annotation.annotation_id for x in page.items] == sorted(
        [second.annotation_id, tied.annotation_id], reverse=True) + [first.annotation_id]
    assert {x.annotation.context_id for x in page.items} == {ctx, 'ctx2'}
    assert {(x.paper_id, x.paper_title, x.version_id, x.file_display_name) for x in page.items} == {
        ('paper', 'Attention Paper', 'v1', 'paper.pdf')}


def test_searches_body_quote_title_and_treats_wildcards_literally(env):
    db, ctx = env
    body = note(db, ctx, 'needle in body')
    quote = note(db, ctx, 'other', segment=SegmentInput('c1', 0, 5, 'First'))
    percent = note(db, ctx, '100% literal')
    note(db, ctx, '1000 unrelated')
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE papers SET title='Transformer Handbook' WHERE paper_id='paper'")

    assert [x.annotation.annotation_id for x in library.list_annotation_library(db, query='needle').items] == [body.annotation_id]
    assert [x.annotation.annotation_id for x in library.list_annotation_library(db, query='First').items] == [quote.annotation_id]
    assert len(library.list_annotation_library(db, query='transformer').items) == 4
    assert [x.annotation.annotation_id for x in library.list_annotation_library(db, query='100%').items] == [percent.annotation_id]


def test_filters_date_kind_and_binds_cursor_to_every_filter(env):
    db, ctx = env
    old = note(db, ctx, 'old')
    middle = note(db, ctx, 'middle')
    newest = note(db, ctx, 'newest')
    at(db, old.annotation_id, '2026-01-01T00:00:00+00:00')
    at(db, middle.annotation_id, '2026-01-02T00:00:00+00:00')
    at(db, newest.annotation_id, '2026-01-03T00:00:00+00:00')

    page = library.list_annotation_library(db, kind='note', created_from='2026-01-02T00:00:00+00:00', limit=1)
    assert page.items[0].annotation.annotation_id == newest.annotation_id and page.next_cursor
    last = library.list_annotation_library(
        db, kind='note', created_from='2026-01-02T00:00:00+00:00', limit=1, cursor=page.next_cursor)
    assert [x.annotation.annotation_id for x in last.items] == [middle.annotation_id]
    assert code_of(lambda: library.list_annotation_library(db, kind='note', query='x', cursor=page.next_cursor)) == INVALID_CURSOR


def test_date_filters_normalize_explicit_offsets_to_utc(env):
    db, ctx = env
    record = note(db, ctx, 'timezone')
    at(db, record.annotation_id, '2026-01-02T00:30:00+00:00')
    page = library.list_annotation_library(
        db, created_from='2026-01-02T09:00:00+09:00', created_before='2026-01-02T10:00:00+09:00')
    assert [x.annotation.annotation_id for x in page.items] == [record.annotation_id]


def test_deleted_anchor_does_not_break_following_page(env):
    db, ctx = env
    records = [note(db, ctx, str(i)) for i in range(4)]
    for i, record in enumerate(records):
        at(db, record.annotation_id, f'2026-01-0{i + 1}T00:00:00+00:00')
    first = library.list_annotation_library(db, limit=2)
    anchor = first.items[-1].annotation
    assert annotations.delete_annotation(db, ctx, anchor.annotation_id, anchor.revision).outcome.value == 'deleted'
    following = library.list_annotation_library(db, limit=2, cursor=first.next_cursor)
    assert [x.annotation.body for x in following.items] == ['1', '0']


def test_global_page_query_uses_library_keyset_index(env):
    db, ctx = env
    with closing(get_connection(db)) as conn, conn:
        conn.executemany(
            'INSERT INTO annotations(annotation_id, context_id, kind, body, step, revision, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, NULL, 1, ?, ?)',
            [(f'bulk-{i:05d}', ctx, 'note', str(i), f'2026-01-{i // 1000 + 1:02d}T00:00:00+00:00',
              f'2026-01-{i // 1000 + 1:02d}T00:00:00+00:00') for i in range(5000)])
        sql, params = library.annotation_library_query(None, None, None, None, 20,
                                                       ('2026-01-03T00:00:00+00:00', 'bulk-02500'))
        plan = ' '.join(str(value) for row in conn.execute('EXPLAIN QUERY PLAN ' + sql, params) for value in row)
    assert 'idx_annotations_library_page' in plan and 'TEMP B-TREE' not in plan, plan


def test_saved_quote_survives_while_current_location_status_changes(env):
    db, ctx = env
    record = note(db, ctx, 'source', segment=SegmentInput('c1', 0, 5, 'First'))
    current = library.list_annotation_library(db).items[0]
    assert current.annotation.annotation_id == record.annotation_id
    assert current.annotation.segments[0].quote_snapshot == 'First'
    assert current.pdf_pages == (1,) and current.segment_available == (True,)
    assert current.location_status == 'available'

    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='Changed.' WHERE chunk_id='c1'")
    changed = library.list_annotation_library(db).items[0]
    assert changed.annotation.segments[0].quote_snapshot == 'First'
    assert changed.segment_available == (False,) and changed.location_status == 'unavailable'


@pytest.mark.parametrize('kwargs', [
    {'created_from': 'not-a-date'},
    {'created_before': '2026-01-01'},
    {'created_from': '2026-01-02T00:00:00+00:00', 'created_before': '2026-01-01T00:00:00+00:00'},
    {'kind': 'unknown'},
    {'query': 3},
])
def test_rejects_invalid_filters(env, kwargs):
    db, _ = env
    with pytest.raises(AnnotationValidationError):
        library.list_annotation_library(db, **kwargs)
