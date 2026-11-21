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
    from concurrent.futures import ThreadPoolExecutor
    concurrent_key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: remote.resolver.pin(
            remote.owner, concurrent_key, binding.paper_id, binding.server_version_id), range(8)))
    assert all(result == results[0] for result in results)
    remote.publish(100, base=first)
    assert remote.resolver.pin(remote.owner, context, binding.paper_id, binding.server_version_id) == pinned
    assert remote.resolver.pin('other', str(uuid4()), binding.paper_id, binding.server_version_id) is None
    with pytest.raises(ValueError, match='remote_context_key_conflict'):
        remote.resolver.pin(remote.owner, context, binding.paper_id, 'wrong-version')
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (binding.paper_id,))
    assert remote.resolver.pin(remote.owner, context, binding.paper_id, binding.server_version_id) is None


def test_default_open_reuses_existing_learning_and_explicit_new_keeps_both(db, stores, pool):
    from concurrent.futures import ThreadPoolExecutor

    remote = PgvectorContractStore(db, stores, pool)
    first = remote.publish()
    binding = remote.resolver.resolve(remote.owner, first.context)

    def default_open(_):
        return remote.resolver.resume_or_pin(
            remote.owner, str(uuid4()), binding.paper_id, binding.server_version_id)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(default_open, range(8))) == [binding] * 8
    new_key = str(uuid4())
    separate = remote.resolver.pin(remote.owner, new_key, binding.paper_id, binding.server_version_id)
    assert separate.context_id == new_key
    assert remote.resolver.resolve(remote.owner, first.context) == binding
    assert remote.resolver.resume_or_pin('other', str(uuid4()), binding.paper_id, binding.server_version_id) is None

    untouched = remote.publish(100)
    db('DELETE FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s',
       (remote.owner, untouched.context))
    with ThreadPoolExecutor(max_workers=4) as executor:
        opened = list(executor.map(lambda _: remote.resolver.resume_or_pin(
            remote.owner, str(uuid4()), untouched.paper, untouched.version), range(8)))
    assert len({entry.context_id for entry in opened}) == 1
    assert len(db('SELECT context_id FROM server_execution_contexts WHERE owner_user_id=%s AND paper_id=%s',
                  (remote.owner, untouched.paper))) == 1
