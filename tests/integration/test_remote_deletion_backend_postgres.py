"""tombstone 뒤 실제 PostgreSQL FK 순서와 객체 정리 경계를 검증한다."""
# ruff: noqa: F401,F811
import os

import pytest

from tests.integration.test_remote_processing_postgres import (
    pool, db, stores, make_paper, claimed_job, make_revision_set, publish, new_id,
)
from solo_leveling.infrastructure.storage.remote_deletion_backend import PostgresObjectDeletionBackend
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore


pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="실제 PostgreSQL DSN 필요")


class Objects:
    def __init__(self):
        self.deleted = []

    def delete_upload(self, key):
        self.deleted.append(key)


def test_backend_removes_children_and_objects_but_preserves_tombstone(db, stores, pool):
    owner = "deletion-backend"
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'synthetic')", (owner,))
    paper = make_paper(db, owner)
    job = claimed_job(stores[0], owner, paper)
    revisions = make_revision_set(db, owner, paper, job, chunks=1)
    publish(stores[1], owner, paper, revisions, job)
    upload, object_key = new_id(), f"private/files/{new_id()}.pdf"
    db(
        """INSERT INTO server_pdf_uploads
           (upload_id,owner_user_id,paper_id,server_version_id,local_version_id,file_name,
            file_size,content_type,sha256_hex,object_key,status,expires_at,completed_at)
           VALUES (%s,%s,%s,'v1','local','paper.pdf',10,'application/pdf',%s,%s,
                   'ready',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
        (upload, owner, paper, "a" * 64, object_key),
    )
    db("UPDATE server_papers SET title='합성 제목',authors='[\"합성 저자\"]',publication_year=2026 WHERE paper_id=%s", (paper,))
    request = PostgresRemoteDeletionStore(pool).request(owner, paper, new_id())
    objects = Objects()
    backend = PostgresObjectDeletionBackend(pool, objects)

    backend.delete_derived(owner, paper)
    assert db("SELECT count(*) AS n FROM server_processing_publications WHERE paper_id=%s", (paper,))[0]["n"] == 0
    assert db("SELECT count(*) AS n FROM server_processing_versions WHERE paper_id=%s", (paper,))[0]["n"] == 1

    # 완료 표식 저장 전에 중단돼도 파생 단계는 다시 실행할 수 있어야 한다.
    backend.delete_derived(owner, paper)
    backend.delete_source(owner, paper)
    assert objects.deleted == [object_key]
    assert db('SELECT title,authors,publication_year FROM server_papers WHERE paper_id=%s', (paper,))[0] == {
        'title': None, 'authors': None, 'publication_year': None}
    assert db("SELECT count(*) AS n FROM server_pdf_uploads WHERE paper_id=%s", (paper,))[0]["n"] == 0
    assert db("SELECT count(*) AS n FROM server_processing_versions WHERE paper_id=%s", (paper,))[0]["n"] == 0
    assert db("SELECT deleted_at IS NOT NULL AS deleted FROM server_papers WHERE paper_id=%s", (paper,))[0]["deleted"]
    assert db("SELECT status FROM server_deletion_requests WHERE request_id=%s", (request.request_id,))[0]["status"] == "blocking"

    # 외부 삭제와 DB 응답 유실 뒤 같은 단계 재실행은 멱등해야 한다.
    backend.delete_derived(owner, paper)
    backend.delete_source(owner, paper)


@pytest.mark.parametrize('published', [False, True])
def test_cleanup_service_handles_unpublished_and_published_paper_without_touching_other(db, stores, pool, published):
    from solo_leveling.infrastructure.storage.remote_deletion_backend import create_remote_deletion_cleanup

    owner = 'service-' + new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'synthetic')", (owner,))
    paper = make_paper(db, owner)
    other = make_paper(db, owner)
    if published:
        job = claimed_job(stores[0], owner, paper)
        revisions = make_revision_set(db, owner, paper, job, chunks=1)
        publish(stores[1], owner, paper, revisions, job)
    request = PostgresRemoteDeletionStore(pool).request(owner, paper, new_id())
    # 이전 사례의 요청은 조정기 대상에서 제외한다. 같은 모듈의 합성 스키마만 사용한다.
    db("UPDATE server_deletion_requests SET status='complete' WHERE request_id<>%s", (request.request_id,))
    service = create_remote_deletion_cleanup(pool, Objects(), worker_id='synthetic', lease_seconds=60)
    assert service.run_once()['status'] == 'complete'
    assert db('SELECT status FROM server_deletion_requests WHERE request_id=%s', (request.request_id,))[0]['status'] == 'complete'
    assert db('SELECT count(*) AS n FROM server_processing_versions WHERE paper_id=%s', (other,))[0]['n'] == 1
    assert service.run_once() == {'status': 'idle'}


def test_tombstone_trigger_rejects_non_pointer_writes_with_fixed_reason(db, stores, pool):
    import psycopg

    owner = 'trigger-' + new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'synthetic')", (owner,))
    paper = make_paper(db, owner)
    job = claimed_job(stores[0], owner, paper)
    PostgresRemoteDeletionStore(pool).request(owner, paper, new_id())
    for sql, args in [
        ('UPDATE server_processing_jobs SET retryable=true WHERE job_id=%s', (job.job_id,)),
        ("UPDATE server_processing_versions SET created_at=created_at+interval '1 second' WHERE paper_id=%s", (paper,)),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation, match='paper_tombstoned'):
            db(sql, args)
