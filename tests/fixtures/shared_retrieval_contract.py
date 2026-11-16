"""두 실제 어댑터가 함께 쓰는 합성 자료·명시적 정답. 모델/검색 대역이 아니다."""
from dataclasses import dataclass
import math
from uuid import UUID
import pytest

QUESTION = '합성 검색 질문'
QUERY_VECTOR = (1., 0.)
MODEL = 'synthetic-contract-model'
FINGERPRINT = dict(model_id=MODEL, model_revision='fixture', tokenizer_id='fixture', tokenizer_revision='fixture',
    pooling='mean', normalization='l2', max_length=128, dimension=2, distance='cosine',
    runtime='synthetic-fixture', runtime_version='1', precision='float32', artifact_sha256='a'*64)


@dataclass(frozen=True)
class Row:
    label: str
    chunk_id: str
    page: int
    section: str
    vector: tuple[float, float]
    translated: bool = True
    source_tag: int = 0

    @property
    def original(self): return f'Synthetic evidence {self.label} in snapshot {self.source_tag}.'

    @property
    def korean(self): return f'합성 근거 {self.label}, 원문 묶음 {self.source_tag}.'


def rows(offset=0, *, partial=False):
    specs = [('a', 6, 's4', (.8, .6)), ('b', 6, 's4', (.6, .8)),
             ('c', 6, 's4', (0., 1.)), ('d', 6, 's4', (0., 1.)),
             ('outside-page', 7, 's4', (1., 0.)), ('outside-section', 6, 's5', (1., 0.)),
             ('opposite', 8, 's6', (-1., 0.))]
    result = tuple(Row(label, str(UUID(int=offset+i)), page, section, vector, source_tag=offset)
                   for i, (label, page, section, vector) in enumerate(specs, 1))
    if partial:
        result += (Row('omitted', str(UUID(int=offset+8)), 6, 's4', (1., 0.), False, offset),)
    return result


@dataclass(frozen=True)
class Case:
    name: str
    pages: tuple[int, ...]
    sections: tuple[str, ...]
    top_k: int
    expected: tuple[str, ...]
    strict_pages: bool = False


# 정답은 검색 결과로 생성하지 않는다. 범위 밖의 완전 일치 벡터를 포함해 사전 범위 계약을 검사한다.
CASES = (
    Case('all-and-ties', (), (), 20, ('outside-page', 'outside-section', 'a', 'b', 'c', 'd', 'opposite')),
    Case('filtered-top-one', (6,), ('s4',), 1, ('a',)),
    Case('page-only', (6,), (), 3, ('outside-section', 'a', 'b')),
    Case('section-only', (), ('s4',), 3, ('outside-page', 'a', 'b')),
    Case('tie-at-cutoff', (6,), ('s4',), 3, ('a', 'b', 'c')),
    Case('larger-than-eligible', (6,), ('s4',), 20, ('a', 'b', 'c', 'd')),
    Case('strict-ordinary-search', (6,), ('s4',), 1, ('a',), True),
    Case('empty-page', (9,), (), 5, ()),
    Case('empty-intersection', (7,), ('s5',), 5, ()),
    Case('opposite-vector', (8,), ('s6',), 1, ('opposite',)),
)


def expected_distance(row, metric):
    """두 거리를 각각 좌표에서 계산한다. 단위 벡터 가정도 이 지점에서 확인한다."""
    assert metric in ('squared_l2', 'cosine')
    qnorm, dnorm = math.hypot(*QUERY_VECTOR), math.hypot(*row.vector)
    assert math.isclose(qnorm, 1., abs_tol=1e-12, rel_tol=1e-12)
    assert math.isclose(dnorm, 1., abs_tol=1e-12, rel_tol=1e-12)
    if metric == 'squared_l2':
        return math.fsum((q-d)**2 for q, d in zip(QUERY_VECTOR, row.vector, strict=True))
    return 1. - math.fsum(q*d for q, d in zip(QUERY_VECTOR, row.vector, strict=True)) / (qnorm * dnorm)


def assert_raw_distances(pairs, snapshot, *, metric):
    expected = {r.chunk_id: r for r in snapshot.rows if r.translated}
    assert len(pairs) == len(expected)
    assert {key for key, _ in pairs} == set(expected)
    for key, distance in pairs:
        assert distance == pytest.approx(expected_distance(expected[key], metric), abs=1e-6, rel=1e-6)


def assert_contract(result, snapshot, case, *, metric):
    """네이티브 점수는 각각 독립 oracle로 검사하고 비교용 cosine 값만 정규화한다."""
    assert metric in ('squared_l2', 'cosine')
    expected = {r.label: r for r in snapshot.rows}
    assert result.query == QUESTION and result.retrieval_method.value == 'vector'
    assert (result.scope.version_id, result.scope.parse_revision_id, result.scope.translation_revision_id,
            result.embedding_set_id) == (snapshot.version, snapshot.parse, snapshot.translation, snapshot.embedding)
    assert result.scope.pdf_pages == case.pages and result.scope.section_ids == case.sections
    assert result.scope.embedding_set_id == snapshot.embedding
    assert result.supplemental_chunks == ()
    assert tuple(i.chunk.chunk_id for i in result.items) == tuple(expected[k].chunk_id for k in case.expected)
    normalized = []
    for rank, (item, label) in enumerate(zip(result.items, case.expected), 1):
        row = expected[label]
        assert item.rank == rank
        assert row.translated
        assert (item.chunk.original_text, item.chunk.text, item.chunk.pdf_page, item.chunk.section_id) == (
            row.original, row.korean, row.page, row.section)
        assert item.chunk.parse_revision_id == snapshot.parse
        assert item.chunk.translation_revision_id == snapshot.translation
        # fixture는 단위 벡터다. Chroma 기본 L2^2 = 2 * cosine distance.
        cosine_distance = expected_distance(row, 'cosine')
        native_distance = expected_distance(row, metric)
        assert item.score == pytest.approx(1 / (1 + native_distance), abs=1e-6, rel=1e-6)
        observed_cosine = (1 / item.score - 1) / (2 if metric == 'squared_l2' else 1)
        assert observed_cosine == pytest.approx(cosine_distance, abs=1e-6)
        normalized.append((label, observed_cosine, 1 / (1 + observed_cosine)))
    return normalized
