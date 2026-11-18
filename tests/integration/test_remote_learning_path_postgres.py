"""실제 게시 작업과 검증 업로드의 해시·소유권 연결을 검사한다."""
# ruff: noqa: F811
from dataclasses import replace
from uuid import uuid4
import os
from unittest.mock import Mock
import pytest
from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from tests.integration.test_processing_publication_postgres import prepared, publish
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries
from solo_leveling.interfaces.mcp.remote_learning_path import RemoteLearningPath

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_published_upload_hash_is_selected_and_foreign_context_hidden(pool, db):
    publisher, source, digest = prepared(pool, db)
    publication = publish(publisher, source, digest)
    context = str(uuid4())
    db('''INSERT INTO server_execution_contexts
        (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)''', (context, source.owner_user_id, source.paper_id,
        source.version_id, publication.publication_id, source.parse_revision_id))
    q = RemoteProductQueries(pool)
    q.owner = Mock(return_value=source.owner_user_id)
    r = RemoteLearningPath(q)
    binding, actual_hash = r._context(context)
    assert binding.publication_id == publication.publication_id
    assert actual_hash == source.pdf_sha256
    # 합성 PDF 해시를 검수 시드에 연결해 ready 경로의 실제 DB 조립을 검사한다.
    r.graph = replace(r.graph, papers=(replace(r.graph.papers[0], file_sha256=actual_hash),))
    assert r.read(context)['result']['graph_status'] == 'ready'
    assert r.read(context)['result']['steps']
    q.owner.return_value = 'another-owner'
    assert r.read(context)['error_code'] == 'NOT_FOUND'
