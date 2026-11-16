"""동일 fixture를 실제 두 backend에서 실행한다. PostgreSQL DSN이 없으면 명시적으로 skip한다."""
# ruff: noqa: F811
import os
import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.fixtures.shared_retrieval_contract import CASES, assert_contract, assert_raw_distances, QUERY_VECTOR
from tests.fixtures.chroma_retrieval_contract import ChromaContractStore
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),
                                reason='실제 PostgreSQL+pgvector 테스트 DSN 필요')


@pytest.fixture
def pair(tmp_path, chroma_client_factory, db, stores, pool):
    local = ChromaContractStore(tmp_path / 'local.sqlite', chroma_client_factory(str(tmp_path / 'chroma')))
    remote = PgvectorContractStore(db, stores, pool)
    return local, remote


def compare(pair, case, *, partial=False):
    actual = []
    for backend in pair:
        snapshot = backend.publish(partial=partial)
        if partial:
            backend.assert_partial(snapshot)
        result = backend.search(snapshot, case)
        actual.append(assert_contract(result, snapshot, case, metric=backend.metric))
    assert [r[0] for r in actual[0]] == [r[0] for r in actual[1]]
    for local, remote in zip(*actual):
        assert local[1:] == pytest.approx(remote[1:], abs=1e-6)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_same_fixture_has_same_allowed_ids_ranks_and_canonical_distances(pair, case):
    compare(pair, case)


@pytest.mark.parametrize('name', ['filtered-top-one', 'all-and-ties'])
def test_partial_published_set_keeps_failed_translation_out_of_both_backends(pair, name):
    compare(pair, next(c for c in CASES if c.name == name), partial=True)


def test_both_old_contexts_stay_pinned_after_new_publication(pair):
    case = next(c for c in CASES if c.name == 'all-and-ties')
    for backend in pair:
        old = backend.publish()
        before = assert_contract(backend.search(old, case), old, case, metric=backend.metric)
        new = backend.publish(100, base=old)
        assert new.embedding != old.embedding and new.parse != old.parse
        assert new.rows[0].original != old.rows[0].original
        assert new.rows[0].korean != old.rows[0].korean
        assert backend.active_embedding(old) == new.embedding
        assert assert_contract(backend.search(old, case), old, case, metric=backend.metric) == before
        assert_contract(backend.search(new, case), new, case, metric=backend.metric)


def test_remote_owner_isolation_is_separate_from_local_single_user_contract(db, stores, pool):
    remote = PgvectorContractStore(db, stores, pool)
    own = remote.publish()
    other = PgvectorContractStore(remote.db, remote.stores, remote.pool)
    foreign = other.publish(1000, vectors=QUERY_VECTOR)
    assert other.resolver.resolve(other.owner, foreign.context) is not None
    assert remote.resolver.resolve(remote.owner, foreign.context) is None
    assert other.resolver.resolve(other.owner, own.context) is None
    # 타인 자료는 질의와 완전 일치하지만 소유자의 근거 top1을 밀어내면 안 된다.
    case = next(c for c in CASES if c.name == 'filtered-top-one')
    control = other.search(foreign, case)
    assert len(control.items) == 1 and control.items[0].chunk.chunk_id == foreign.rows[0].chunk_id
    assert control.items[0].score == pytest.approx(1.)
    assert_contract(remote.search(own, case), own, case, metric=remote.metric)


def test_native_scores_intentionally_differ_for_same_unit_vectors(pair):
    case = next(c for c in CASES if c.name == 'opposite-vector')
    results = [backend.search(backend.publish(), case) for backend in pair]
    for result in results:
        assert result.items[0].rank == 1
    assert results[0].items[0].score == pytest.approx(1/5)
    assert results[1].items[0].score == pytest.approx(1/3)


def test_both_raw_distances_match_independent_geometry(pair):
    for backend in pair:
        snapshot = backend.publish()
        assert_raw_distances(backend.native_distances(snapshot), snapshot, metric=backend.metric)
