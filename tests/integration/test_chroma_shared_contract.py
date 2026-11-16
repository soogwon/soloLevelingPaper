"""동일한 고정 벡터/명시적 정답으로 실제 로컬 어댑터를 검증한다."""
from dataclasses import replace
import math

import pytest

from tests.fixtures.chroma_retrieval_contract import ChromaContractStore
from tests.fixtures.shared_retrieval_contract import CASES, assert_contract, assert_raw_distances


@pytest.fixture
def local_store(tmp_path, chroma_client_factory):
    return ChromaContractStore(tmp_path / 'local.sqlite', chroma_client_factory(str(tmp_path / 'chroma')))


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_chroma_passes_shared_contract(local_store, case):
    snapshot = local_store.publish()
    assert_contract(local_store.search(snapshot, case), snapshot, case, metric=local_store.metric)


@pytest.mark.parametrize('name', ['filtered-top-one', 'all-and-ties'])
def test_partial_index_does_not_search_failed_translation(local_store, name):
    snapshot = local_store.publish(partial=True)
    local_store.assert_partial(snapshot)
    case = next(c for c in CASES if c.name == name)
    assert_contract(local_store.search(snapshot, case), snapshot, case, metric=local_store.metric)


def test_old_context_keeps_source_and_index_after_reparse(local_store):
    old = local_store.publish()
    case = next(c for c in CASES if c.name == 'all-and-ties')
    before = assert_contract(local_store.search(old, case), old, case, metric=local_store.metric)
    new = local_store.publish(100, base=old)
    assert new.embedding != old.embedding and new.parse != old.parse
    assert new.rows[0].original != old.rows[0].original
    assert new.rows[0].korean != old.rows[0].korean
    assert local_store.active_embedding(old) == new.embedding
    assert assert_contract(local_store.search(old, case), old, case, metric=local_store.metric) == before
    assert_contract(local_store.search(new, case), new, case, metric=local_store.metric)


def test_chroma_raw_distances_are_squared_l2_not_euclidean_or_cosine(local_store):
    snapshot = local_store.publish()
    distances = local_store.native_distances(snapshot)
    assert_raw_distances(distances, snapshot, metric=local_store.metric)
    # 실제 반환값을 잘못된 metric으로 바꾸면 같은 순위여도 거부해야 한다.
    for transform in (math.sqrt, lambda d: d / 2):
        with pytest.raises(AssertionError):
            assert_raw_distances(tuple((key, transform(d)) for key, d in distances),
                                 snapshot, metric=local_store.metric)


def test_contract_oracle_rejects_corrupt_scores_order_rank_and_source(local_store):
    snapshot = local_store.publish()
    case = next(c for c in CASES if c.name == 'larger-than-eligible')
    result = local_store.search(snapshot, case)
    assert_contract(result, snapshot, case, metric=local_store.metric)
    first = result.items[0]
    native_distance = 1 / first.score - 1
    wrong_scores = (1 / (1 + native_distance / 2),  # cosine를 L2 점수로 오인
                    1 / (1 + math.sqrt(native_distance)),  # 제곱하지 않은 L2
                    1 - native_distance)  # 잘못된 점수 변환
    mutations = [replace(result, items=(replace(first, score=score), *result.items[1:]))
                 for score in wrong_scores]
    mutations += [
        replace(result, items=tuple(reversed(result.items))),
        replace(result, items=(replace(first, rank=2), *result.items[1:])),
        replace(result, scope=replace(result.scope, translation_revision_id='wrong-revision')),
        replace(result, embedding_set_id='wrong-set'),
    ]
    for field, value in (('original_text', 'Other snapshot original.'), ('text', '다른 번역'),
                         ('parse_revision_id', 'wrong-parse'), ('translation_revision_id', 'wrong-translation'),
                         ('pdf_page', 7), ('section_id', 's5')):
        mutations.append(replace(result, items=(
            replace(first, chunk=replace(first.chunk, **{field: value})), *result.items[1:])))
    for corrupted in mutations:
        with pytest.raises(AssertionError):
            assert_contract(corrupted, snapshot, case, metric=local_store.metric)
