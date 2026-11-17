"""학습 기록 없는 원격 PDF의 웹 표시와 판본·소유자 경계를 검증한다."""
# ruff: noqa: F811
import os
from uuid import UUID, uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.file_uploads import PostgresFileUploadStore, UploadTargetNotFound
from solo_leveling.interfaces.http.papers import PostgresPaperStore, PaperNotFound
from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore
from solo_leveling.interfaces.http.remote_upload_consents import PostgresRemoteUploadConsentStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def test_remote_pdf_without_learning_record_is_visible_and_owned(pool, db):
    owner = 'remote-detail-' + str(uuid4())
    db('INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,%s)', (owner, '합성'))
    uploads = PostgresRemoteUploadIntentStore(pool)
    intent = uploads.create(owner, 'key', 'synthetic.pdf', 10, 'application/pdf', 'a' * 64)
    papers = PostgresPaperStore(pool)
    files = PostgresFileUploadStore(pool)
    def detail(version=None):
        return papers.get_paper(owner, intent.paper_id, learning_record_id=None, version_id=version).paper
    assert detail().selected_version_id is None
    PostgresRemoteUploadConsentStore(pool).grant(owner, intent.intent_id, 'storage', 'consent')
    uploads.reserve_upload(owner, intent.intent_id)
    assert detail().selected_version_id is None
    uploads.mark_verified(owner, intent.intent_id, 'private/files/synthetic.pdf')
    for version in (None, intent.server_version_id):
        item = detail(version)
        assert item.file_name == 'synthetic.pdf'
        assert item.title is None
        assert item.selected_version_id == intent.server_version_id
        assert item.selected_learning_record_id is None
        assert item.reading_status == 'unknown'
    page = papers.list_papers(owner, query=None, reading_status=None, page=1, limit=20)
    assert page.items[0].file_name == 'synthetic.pdf'
    assert page.items[0].selected_version_id == intent.server_version_id
    assert files.get_file_access(owner, intent.paper_id, intent.server_version_id).status == 'ready'
    route = next(r.endpoint for r in create_app(paper_store=papers, file_upload_store=files).routes
                 if r.path == '/v1/papers/{paper_id}')
    view = route(UUID(intent.paper_id), AuthenticatedPrincipal(owner, 'web_session'), None, None)
    assert view.file_access.available
    assert not view.resume.available
    assert view.resume.reason == 'learning_record_missing'
    for bad_owner, bad_version in [('other-owner', intent.server_version_id), (owner, 'other-version')]:
        with pytest.raises(UploadTargetNotFound):
            files.get_file_access(bad_owner, intent.paper_id, bad_version)
        with pytest.raises(PaperNotFound):
            papers.get_paper(bad_owner, intent.paper_id, learning_record_id=None, version_id=bad_version)
    with pytest.raises(PaperNotFound):
        papers.get_paper(owner, intent.paper_id, learning_record_id=str(uuid4()), version_id=None)
    # 더 최근 판본이 있어도 명시한 판본을 다른 파일로 대체하지 않는다.
    other_version = str(uuid4())
    db('INSERT INTO server_processing_versions(owner_user_id,paper_id,server_version_id) VALUES (%s,%s,%s)',
       (owner, intent.paper_id, other_version))
    db("""INSERT INTO server_pdf_uploads
          (upload_id,owner_user_id,paper_id,server_version_id,file_name,file_size,
           content_type,sha256_hex,object_key,status,expires_at,source_kind,updated_at)
          SELECT %s,owner_user_id,paper_id,%s,'second.pdf',file_size,content_type,
                 sha256_hex,'private/files/second.pdf','ready',expires_at,'remote',
                 CURRENT_TIMESTAMP + interval '1 second'
          FROM server_pdf_uploads WHERE owner_user_id=%s AND paper_id=%s""",
       (str(uuid4()), other_version, owner, intent.paper_id))
    assert detail().selected_version_id == other_version
    assert detail(intent.server_version_id).file_name == 'synthetic.pdf'
    assert files.get_file_access(owner, intent.paper_id, intent.server_version_id).object_key == 'private/files/synthetic.pdf'
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (intent.paper_id,))
    with pytest.raises(PaperNotFound):
        detail()
    with pytest.raises(UploadTargetNotFound):
        files.get_file_access(owner, intent.paper_id, intent.server_version_id)
    assert papers.list_papers(owner, query=None, reading_status=None, page=1, limit=20).total_count == 0
