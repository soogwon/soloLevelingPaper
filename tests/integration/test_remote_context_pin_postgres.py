"""원격 context를 멱등 고정하고 재게시·타인·삭제 경계를 확인한다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def test_context_pin_preserves_old_publication_and_rejects_other_scope(db, stores, pool):
    remote = PgvectorContractStore(db, stores, pool)
    first = remote.publish()
    context = str(uuid4())
    binding = remote.resolver.resolve(remote.owner, first.context)
    pinned = remote.resolver.pin(remote.owner, context, binding.paper_id, binding.server_version_id)
    assert pinned.publication_id == binding.publication_id
    remote.publish(100, base=first)
    assert remote.resolver.pin(remote.owner, context, binding.paper_id, binding.server_version_id) == pinned
    assert remote.resolver.pin('other', str(uuid4()), binding.paper_id, binding.server_version_id) is None
    with pytest.raises(ValueError, match='remote_context_key_conflict'):
        remote.resolver.pin(remote.owner, context, binding.paper_id, 'wrong-version')
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (binding.paper_id,))
    assert remote.resolver.pin(remote.owner, context, binding.paper_id, binding.server_version_id) is None
