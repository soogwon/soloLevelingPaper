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
    request = PostgresRemoteDeletionStore(pool).request(owner, paper, new_id())
    objects = Objects()
    backend = PostgresObjectDeletionBackend(pool, objects)

    backend.delete_derived(owner, paper)
    assert db("SELECT count(*) AS n FROM server_processing_publications WHERE paper_id=%s", (paper,))[0]["n"] == 0
    assert db("SELECT count(*) AS n FROM server_processing_versions WHERE paper_id=%s", (paper,))[0]["n"] == 1

    backend.delete_source(owner, paper)
    assert objects.deleted == [object_key]
    assert db("SELECT count(*) AS n FROM server_pdf_uploads WHERE paper_id=%s", (paper,))[0]["n"] == 0
    assert db("SELECT count(*) AS n FROM server_processing_versions WHERE paper_id=%s", (paper,))[0]["n"] == 0
    assert db("SELECT deleted_at IS NOT NULL AS deleted FROM server_papers WHERE paper_id=%s", (paper,))[0]["deleted"]
    assert db("SELECT status FROM server_deletion_requests WHERE request_id=%s", (request.request_id,))[0]["status"] == "blocking"

    # 외부 삭제와 DB 응답 유실 뒤 같은 단계 재실행은 멱등해야 한다.
    backend.delete_derived(owner, paper)
    backend.delete_source(owner, paper)
