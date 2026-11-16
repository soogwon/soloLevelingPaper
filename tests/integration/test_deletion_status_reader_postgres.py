"""실제 PostgreSQL에서 상태 조회가 요청을 만들거나 갱신하지 않음을 확인한다."""
# ruff: noqa: F811
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, make_paper  # noqa: F401
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore
from solo_leveling.interfaces.mcp.deletion_guidance import DeletionLookup, recover_deletion_guidance
from solo_leveling.interfaces.mcp.deletion_status_reader import OwnerScopedDeletionReader

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def test_exact_owner_paper_key_lookup_is_read_only(db, pool):
    owner = 'status-owner'
    paper, other = make_paper(db, owner), make_paper(db, owner)
    store = PostgresRemoteDeletionStore(pool)
    reader = OwnerScopedDeletionReader(store, lambda: owner)
    lookup = DeletionLookup(owner, paper, 'key')
    assert recover_deletion_guidance(reader, lookup).state == 'not_found'
    assert db('SELECT deleted_at FROM server_papers WHERE paper_id=%s', (paper,))[0]['deleted_at'] is None
    assert db('SELECT count(*) AS n FROM server_deletion_requests')[0]['n'] == 0
    request = store.request(owner, paper, 'key')
    before = db('SELECT * FROM server_deletion_requests WHERE request_id=%s', (request.request_id,))
    for _ in range(2):
        assert recover_deletion_guidance(reader, lookup).state == 'blocking'
    assert store.get_existing('other-owner', paper, 'key') is None
    assert store.get_existing(owner, other, 'key') is None
    assert store.get_existing(owner, paper, 'different-key') is None
    assert db('SELECT * FROM server_deletion_requests WHERE request_id=%s', (request.request_id,)) == before
    assert db('SELECT count(*) AS n FROM server_deletion_requests')[0]['n'] == 1
    assert db('SELECT deleted_at FROM server_papers WHERE paper_id=%s', (other,))[0]['deleted_at'] is None
