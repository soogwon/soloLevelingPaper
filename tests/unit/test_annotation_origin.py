"""최초 작성 주체 신고와 소유권을 분리하고 재시도·수정·동기화에서 보존한다."""
from contextlib import closing
import pytest
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.application.server_sync.paper_metadata import _annotation_payload
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from solo_leveling.infrastructure.database.annotation_library import list_annotation_library
from solo_leveling.domain.annotation import AnnotationValidationError
from tests.unit.test_annotations import env
from tests.unit.test_learning_persistence import setup_db


@pytest.mark.parametrize('origin', ['user', 'ai', 'unknown'])
def test_origin_persists_and_is_part_of_idempotency(env, origin):
    db, ctx = env
    service = AnnotationService(db)
    saved = service.create(ctx, 'note', '메모', creation_origin=origin, request_key='origin').annotation
    init_db(db)
    replay = service.create(ctx, 'note', '메모', creation_origin=origin, request_key='origin')
    assert replay.reused and replay.annotation == saved
    conflict = service.create(ctx, 'note', '메모', creation_origin='ai' if origin != 'ai' else 'user', request_key='origin')
    assert conflict.code == 'REQUEST_KEY_CONFLICT'
    edited = service.update(ctx, saved.annotation_id, 1, body='수정').annotation
    assert edited.creation_origin == origin
    assert list_annotation_library(db).items[0].annotation.creation_origin == origin
    with closing(get_connection(db)) as conn:
        assert _annotation_payload(conn, 'paper', saved.annotation_id, 'source')['creation_origin'] == origin


def test_legacy_origin_is_unknown_and_bad_origin_is_rejected(env):
    db, ctx = env
    service = AnnotationService(db)
    saved = service.create(ctx, 'note', '기존', request_key='legacy').annotation
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DELETE FROM annotation_creation_origins')
    assert service.get(ctx, saved.annotation_id).annotation.creation_origin == 'unknown'
    assert service.create(ctx, 'note', '기존', request_key='legacy').reused
    with pytest.raises(AnnotationValidationError):
        service.create(ctx, 'note', '오류', creation_origin='owner-admin')


def test_server_origin_conflict_and_legacy_preservation():
    from solo_leveling.interfaces.http.papers import AnnotationMetadata, AnnotationConflict, PostgresPaperStore
    from tests.unit.test_http_papers import Pool, Result, NOW, PAPER_ID, SOURCE_ID
    item = AnnotationMetadata(SOURCE_ID, 'ctx', 'a', 'v1', 'note', '메모', None, (), NOW, NOW, 'ai')
    row = {'annotation_id':PAPER_ID,'revision':1,'updated_at':NOW,'paper_id':PAPER_ID,
           'selected_version_id':'v1','local_learning_record_id':'ctx','creation_origin':'user'}
    pool = Pool([Result({'learning_record_id':PAPER_ID,'selected_version_id':'v1'}), Result(), Result(row)])
    with pytest.raises(AnnotationConflict):
        PostgresPaperStore(pool).upsert_annotation('owner', PAPER_ID, item)
    assert 'excluded.creation_origin=server_annotations.creation_origin' in pool.conn.calls[1][0]
    assert pool.conn.calls[1][1][-2:] == ('ai', False)
    from dataclasses import replace
    old = replace(item, creation_origin=None)
    pool = Pool([Result({'learning_record_id':PAPER_ID,'selected_version_id':'v1'}), Result(row)])
    PostgresPaperStore(pool).upsert_annotation('owner', PAPER_ID, old)
    assert pool.conn.calls[1][1][-2:] == (None, True)
