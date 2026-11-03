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
    assert service.list(ctx, step='preview', kind='note') == [updated]
    cleared = service.update(ctx, key, 2, step=None).annotation
    assert (cleared.body, cleared.step) == ('수정', None)
    assert service.delete(ctx, key, 3).outcome == AnnotationOutcome.DELETED
    assert service.list(ctx) == []


def test_foreign_context_and_missing_list(env):
    db, ctx = env
    service = AnnotationService(db)
    other = repository.get_or_create_default_context(db, 'v1').context_id
    key = service.create(ctx, 'note', '비공개').annotation.annotation_id
    with pytest.raises(ResourceNotFoundError):
        service.get(other, key)
    assert service.list(other) == []
    assert service.update(other, key, 1, body='다른 메모').outcome == AnnotationOutcome.NOT_FOUND
    assert service.delete(other, key, 1).outcome == AnnotationOutcome.NOT_FOUND
    with pytest.raises(ResourceNotFoundError):
        service.list('missing')


def test_translation_segments_are_not_reported_as_verified(env):
    db, ctx = env
    service = AnnotationService(db)
    translated = SegmentInput('c1', 0, 1, '첫', 'translation', 't1')
    with pytest.raises(AnnotationValidationError):
        service.create(ctx, 'highlight', '', segments=[translated])
    assert service.list(ctx) == []
    existing = store.create_annotation(db, ctx, 'highlight', '', [translated]).annotation
    assert service.get(ctx, existing.annotation_id).location_status == 'unavailable'


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


@pytest.mark.parametrize('segments', [None, '', b''])
def test_invalid_segment_list_does_not_create_unanchored_note(env, segments):
    db, ctx = env
    service = AnnotationService(db)
    with pytest.raises(AnnotationValidationError):
        service.create(ctx, 'note', '메모', segments=segments)
    assert service.list(ctx) == []


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
