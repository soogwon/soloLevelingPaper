"""0032 적용 후 실제 PostgreSQL에서 최초 신고 주체와 소유권을 분리한다."""
# ruff: noqa: F811

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, make_paper, new_id  # noqa: F401
from solo_leveling.interfaces.http.papers import AnnotationMetadata, AnnotationConflict, PaperNotFound, PostgresPaperStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),
                                reason='실제 PostgreSQL 테스트 DSN 필요')


@pytest.mark.parametrize('origin', ['user', 'ai', None])
def test_origin_is_immutable_legacy_omission_preserves_it_and_foreign_owner_is_denied(pool, db, origin):
    paper = make_paper(db, 'alice')
    source = str(db('SELECT source_instance_id FROM server_papers WHERE paper_id=%s', (paper,))[0]['source_instance_id'])
    now = datetime.now(timezone.utc)
    db('''INSERT INTO server_learning_records
        (learning_record_id,owner_user_id,paper_id,source_instance_id,local_learning_record_id,
         selected_version_id,reading_status,source_updated_at)
        VALUES (%s,'alice',%s,%s,'ctx','v1','reading',%s)''', (new_id(), paper, source, now))
    store = PostgresPaperStore(pool)
    item = AnnotationMetadata(source, 'ctx', 'annotation', 'v1', 'note', '합성 메모', None, (), now, now, origin)
    saved = store.upsert_annotation('alice', paper, item)
    assert store.get_annotation('alice', saved.annotation_id).creation_origin == (origin or 'unknown')
    legacy = replace(item, body='구형 클라이언트의 수정', creation_origin=None,
                     source_updated_at=now + timedelta(seconds=1))
    updated = store.upsert_annotation('alice', paper, legacy)
    assert updated.annotation_id == saved.annotation_id
    assert store.get_annotation('alice', saved.annotation_id).creation_origin == (origin or 'unknown')
    with pytest.raises(AnnotationConflict):
        store.upsert_annotation('alice', paper, replace(legacy, creation_origin='ai' if origin != 'ai' else 'user',
                                                      source_updated_at=now + timedelta(seconds=2)))
    assert store.get_annotation('alice', saved.annotation_id).body == legacy.body
    with pytest.raises(PaperNotFound):
        store.get_annotation('bob', saved.annotation_id)
    assert db('SELECT count(*) AS n FROM server_annotations WHERE paper_id=%s', (paper,))[0]['n'] == 1
