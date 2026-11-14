"""메모 서비스의 재조회, 위치 확인, 부분 수정과 맥락 격리를 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.annotation import AnnotationOutcome, AnnotationValidationError, SegmentInput
from solo_leveling.infrastructure.database import annotations as store, repository
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_annotations import env
from tests.unit.test_learning_persistence import setup_db


def test_reopen_and_check_each_original_segment(env):
    db, ctx = env
    segments = (SegmentInput('c3', 1, 2, '🙂'), SegmentInput('c1', 0, 5, 'First'))
    saved = AnnotationService(db).create(ctx, 'highlight', '', segments=segments).annotation
    reopened = AnnotationService(db)
    detail = reopened.get(ctx, saved.annotation_id)
    assert detail.annotation == saved
    assert detail.location_status == 'available'
    assert detail.segment_available == (True, True)
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text=' First' WHERE chunk_id='c1'")
    changed = reopened.get(ctx, saved.annotation_id)
    assert changed.annotation == saved
    assert changed.segment_available == (True, False)
    assert changed.location_status == 'unavailable'


def test_partial_update_and_delete_respect_revision(env):
    db, ctx = env
    service = AnnotationService(db)
    saved = service.create(ctx, 'note', '내 메모', step='preview').annotation
    key = saved.annotation_id
    assert service.get(ctx, key).location_status == 'not_applicable'
    updated = service.update(ctx, key, 1, body='수정').annotation
    assert (updated.body, updated.step, updated.revision) == ('수정', 'preview', 2)
    assert service.update(ctx, key, 1, step=None).outcome == AnnotationOutcome.CONFLICT
    assert service.delete(ctx, key, 1).outcome == AnnotationOutcome.CONFLICT
    assert service.list(ctx, step='preview', kind='note').items == (updated,)
    cleared = service.update(ctx, key, 2, step=None).annotation
    assert (cleared.body, cleared.step) == ('수정', None)
    assert service.delete(ctx, key, 3).outcome == AnnotationOutcome.DELETED
    assert service.list(ctx).items == ()


def test_foreign_context_and_missing_list(env):
    db, ctx = env
    service = AnnotationService(db)
    other = repository.get_or_create_default_context(db, 'v1').context_id
    key = service.create(ctx, 'note', '비공개').annotation.annotation_id
    with pytest.raises(ResourceNotFoundError):
        service.get(other, key)
    assert service.list(other).items == ()
    assert service.update(other, key, 1, body='다른 메모').outcome == AnnotationOutcome.NOT_FOUND
    assert service.delete(other, key, 1).outcome == AnnotationOutcome.NOT_FOUND
    with pytest.raises(ResourceNotFoundError):
        service.list('missing')


def test_translation_segments_are_verified_against_stored_revision(env):
    db, ctx = env
    service = AnnotationService(db)
    translated = SegmentInput('c1', 0, 1, '첫', 'translation', 't1')
    saved = service.create(ctx, 'highlight', '', segments=[translated]).annotation
    assert service.get(ctx, saved.annotation_id).segment_available == (True,)
    existing = store.create_annotation(db, ctx, 'highlight', '', [translated]).annotation
    assert service.get(ctx, existing.annotation_id).location_status == 'available'


def test_partial_update_only_copies_fields_from_expected_revision(env, monkeypatch):
    db, ctx = env
    service = AnnotationService(db)
    key = service.create(ctx, 'note', '최초 본문').annotation.annotation_id
    read = store.get_annotation

    def read_then_edit(db_path, context_id, annotation_id):
        snapshot = read(db_path, context_id, annotation_id)
        store.update_annotation(db_path, context_id, annotation_id, 1, '동시 수정')
        return snapshot

    monkeypatch.setattr(store, 'get_annotation', read_then_edit)
    assert service.update(ctx, key, 2, step='preview').outcome == AnnotationOutcome.CONFLICT
    assert read(db, ctx, key).body == '동시 수정'


@pytest.mark.parametrize('segments', [None, '', b'', bytearray(), memoryview(b'')])
def test_invalid_segment_list_does_not_create_unanchored_note(env, segments):
    db, ctx = env
    service = AnnotationService(db)
    with pytest.raises(AnnotationValidationError):
        service.create(ctx, 'note', '메모', segments=segments)
    assert service.list(ctx).items == ()


def test_partial_update_checks_revision_format_even_for_missing_annotation(env):
    db, ctx = env
    with pytest.raises(AnnotationValidationError):
        AnnotationService(db).update(ctx, 'missing', True, body='메모')


@pytest.mark.parametrize('invalid_context', [None, '', '  '])
def test_service_requires_context_for_every_operation(env, invalid_context):
    db, ctx = env
    service = AnnotationService(db)
    saved = service.create(ctx, 'note', '보호할 메모').annotation
    key = saved.annotation_id
    for operation in (
        lambda: service.get(invalid_context, key),
        lambda: service.list(invalid_context),
        lambda: service.update(invalid_context, key, 1, body='변경'),
        lambda: service.delete(invalid_context, key, 1),
        lambda: service.create(invalid_context, 'note', '메모'),
    ):
        with pytest.raises(AnnotationValidationError):
            operation()
    assert service.get(ctx, key).annotation == saved


def test_page_cursor_and_limit_contract(env):
    db, ctx = env
    service = AnnotationService(db)
    keys = {service.create(ctx, 'note', str(n)).annotation.annotation_id for n in range(21)}
    first = service.list(ctx, kind='note')
    assert len(first.items) == 20 and first.next_cursor
    last = AnnotationService(db).list(ctx, kind='note', cursor=first.next_cursor)
    assert len(last.items) == 1 and last.next_cursor is None
    assert {a.annotation_id for a in first.items + last.items} == keys
    for kwargs, code in [({'cursor': first.next_cursor}, 'INVALID_CURSOR'), ({'limit': 51}, 'INVALID_LIMIT')]:
        with pytest.raises(AnnotationValidationError) as caught:
            service.list(ctx, **kwargs)
        assert caught.value.code == code


def test_mixed_translation_creation_and_immutable_segments(env, monkeypatch):
    db, ctx = env
    service = AnnotationService(db)
    original = SegmentInput('c1', 0, 5, 'First')
    translated = SegmentInput('c1', 0, 1, '첫', 'translation', 't1')
    saved = service.create(ctx, 'note', '기존', segments=[original]).annotation
    mixed = service.create(ctx, 'note', '혼합', segments=[original, translated]).annotation
    assert service.get(ctx, mixed.annotation_id).segment_available == (True, True)
    with monkeypatch.context() as guard:
        for name in ('get_annotation', 'update_annotation', 'create_annotation'):
            guard.setattr(store, name, lambda *a, **kw: pytest.fail('unexpected store call'))
        for action in (
            lambda: service.update(ctx, saved.annotation_id, 1, body='수정', segments=[original, translated]),
        ):
            with pytest.raises(AnnotationValidationError) as caught:
                action()
            assert caught.value.code is None
        with pytest.raises(AnnotationValidationError) as caught:
            service.update(ctx, saved.annotation_id, 1, body='수정', segments=[original])
        assert caught.value.code is None
    assert service.list(ctx).items == (saved, mixed)


def test_create_preserves_repository_limit_codes(env):
    db, ctx = env
    service = AnnotationService(db)
    segment = SegmentInput('c1', 0, 5, 'First')
    for segments, code in [
        ([segment, segment], 'DUPLICATE_SEGMENT'),
        ([SegmentInput('c1', n, n + 2000, 'x' * 2000) for n in range(5)], 'QUOTE_TOTAL_TOO_LONG'),
    ]:
        with pytest.raises(AnnotationValidationError) as caught:
            service.create(ctx, 'highlight', '', segments=segments)
        assert caught.value.code == code
    assert service.list(ctx).items == ()


def test_long_unicode_context_can_follow_its_own_cursor(env):
    from solo_leveling.domain.models import LearningContext
    db, other = env
    ctx = '가' * 64
    repository.create_learning_context(db, LearningContext(ctx, 'v1', 'p1', 't1', embedding_set_id='idx'))
    service = AnnotationService(db)
    keys = {service.create(ctx, 'note', str(n)).annotation.annotation_id for n in range(2)}
    first = service.list(ctx, limit=1)
    assert first.next_cursor
    last = service.list(ctx, limit=1, cursor=first.next_cursor)
    assert len(first.items) == len(last.items) == 1 and last.next_cursor is None
    assert {first.items[0].annotation_id, last.items[0].annotation_id} == keys
    with pytest.raises(AnnotationValidationError) as error:
        service.list(other, cursor=first.next_cursor)
    assert error.value.code == 'INVALID_CURSOR'
