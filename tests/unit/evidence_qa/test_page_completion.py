"""페이지 밖에서는 연결 문장만 생성·검증·저장에 사용한다."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.continuation import supplement_continuations
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.application.evidence_qa.page_completion import completion_excerpt
from solo_leveling.application.evidence_qa.original_quotes import original_quotes
from solo_leveling.application.evidence_qa.validators import validate_citation, validate_search_result
from solo_leveling.domain.evidence_qa import (
    Citation, Claim, ClaimSupport, GeneratedAnswerDraft, GeneratedClaim, RetrievedChunk, RetrievalMethod,
    SearchResult, SearchScope,
)
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever


@pytest.fixture
def pair():
    first = Chunk('a', 'p', 10, 'A is faster than B when sequence', '조건 앞부분', 't', pdf_page=6, section_id='method')
    next_chunk = Chunk('b', 'p', 11, 'length n is smaller than d. A also saves memory.',
                       '길이 조건. 메모리도 줄인다.', 't', pdf_page=7, section_id='method')
    search = SearchResult('조건은?', SearchScope('v', 'p', 't', (6,)),
        RetrievalMethod.VECTOR, 'idx', (RetrievedChunk(first, .9, 1),))
    return search, next_chunk


def retriever(pair):
    search, next_chunk = pair
    index = {'embedding_set_id': 'idx'}
    reader = SQLiteChromaRetriever('unused', SimpleNamespace(get_collection=lambda name: object()))
    reader._snapshot = lambda scope: (index, {c.chunk_id: c for c in (search.items[0].chunk, next_chunk)})
    reader._verify_vectors = lambda *args: None
    return reader


def test_reader_only_relaxes_completion_not_general_search(pair):
    search, next_chunk = pair
    reader = retriever(pair)
    assert reader.following_chunks(search, (11,)) == ()
    result = supplement_continuations(search, reader)
    assert result.scope.pdf_pages == (6,)
    assert result.supplemental_chunks == (next_chunk,)
    assert completion_excerpt(result, next_chunk) == 'length n is smaller than d.'
    # 검색 결과 자체의 범위 검증은 느슨하게 하지 않는다.
    with pytest.raises(ValueError):
        validate_search_result(replace(result, items=(RetrievedChunk(next_chunk, .9, 1),), supplemental_chunks=()))


def test_strict_pages_prevents_outside_lookup_result(pair):
    search, _ = pair
    result = supplement_continuations(search, retriever(pair), allow_page_completion=False)
    assert not result.supplemental_chunks


@pytest.mark.parametrize('changes', [
    {'original_text': 'A different complete sentence.'},
    {'original_text': 'length remains incomplete'},
    {'pdf_page': 8}, {'chunk_index': 12}, {'section_id': 'next-section'},
    {'parse_revision_id': 'other'}, {'translation_revision_id': 'other'},
])
def test_no_completion_for_unrelated_or_unfinished_neighbor(pair, changes):
    search, next_chunk = pair
    assert completion_excerpt(replace(search, allow_page_completion=True), replace(next_chunk, **changes)) is None


def test_decimal_and_line_breaks_are_preserved(pair):
    search, next_chunk = pair
    next_chunk = replace(next_chunk, original_text='length n is below\n1.5 times d. Other explanation.')
    assert completion_excerpt(replace(search, allow_page_completion=True), next_chunk) == 'length n is below\n1.5 times d.'


@pytest.mark.parametrize('text,excerpt', [
    ('length n is smaller than d. A also saves memory.', 'length n is smaller than d.'),
    ('length n is smaller than d.A also saves memory.', 'length n is smaller than d.'),
    ('length n is smaller than d。 A also saves memory.', 'length n is smaller than d。'),
    ('length n is below Eq. 3, which is d. A also saves memory.',
     'length n is below Eq. 3, which is d.'),
])
def test_generator_and_saved_citation_only_receive_completion(pair, text, excerpt):
    search, next_chunk = pair
    next_chunk = replace(next_chunk, original_text=text)
    pair = search, next_chunk
    saved = []
    def generate(question, evidence):
        assert len(evidence) == 2
        assert evidence[1].original_text == excerpt
        assert evidence[1].text_ko is None
        assert evidence[1].pdf_page == 7 and evidence[1].chunk_id == 'b'
        assert evidence[1].follows_evidence_id == evidence[0].evidence_id
        options = original_quotes((Claim('claim', '조건 비교', tuple(e.evidence_id for e in evidence)),), evidence)
        assert all(q['text'] in excerpt for q in options if q['evidence_id'] == evidence[1].evidence_id)
        return GeneratedAnswerDraft((GeneratedClaim('n이 d보다 작을 때 A가 더 빠르다.',
            tuple(e.evidence_id for e in evidence),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in evidence)),))
    service = AnswerService(SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', search)),
        SimpleNamespace(generate_claims=generate), SimpleNamespace(save=lambda ctx, rows: saved.extend(rows)),
        continuation_reader=retriever(pair))
    response = service.answer('조건은?', context_id='ctx', pdf_pages=(6,))
    assert response.result.status.value == 'ok'
    assert [(c.chunk_id, c.pdf_page) for c in response.result.citations] == [('a', 6), ('b', 7)]
    citation = response.result.citations[1]
    assert citation.quote_original == saved[1].quote_original == excerpt
    assert citation.quote_ko is None
    assert next_chunk.original_text.endswith('A also saves memory.')
    # 전체 원문을 가진 저장 청크라도 허용 구절 밖의 인용은 거부한다.
    with pytest.raises(ValueError):
        validate_citation(replace(citation, quote_original='A also saves memory.'), response.search)
    with pytest.raises(ValueError):
        validate_citation(replace(citation, quote_ko=next_chunk.text), response.search)


def test_strict_pages_reaches_answer_service(pair):
    search, _ = pair
    counts = []
    def generate(question, evidence):
        counts.append(len(evidence))
        return GeneratedAnswerDraft(())
    service = AnswerService(SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', search)),
        SimpleNamespace(generate_claims=generate), SimpleNamespace(save=lambda *args: pytest.fail('저장 금지')),
        continuation_reader=retriever(pair))
    service.answer('조건은?', context_id='ctx', pdf_pages=(6,), strict_pages=True)
    assert counts == [1]


@pytest.mark.parametrize('text,expected', [
    ('length n is below .5. Other claim.', 'length n is below .5.'),
    ('length n is below d.Other claim.', 'length n is below d.'),
    ('length n is below d。 Other claim.', 'length n is below d。'),
    ('length n is below the limit in Eq. 3, which is d. Other claim.',
     'length n is below the limit in Eq. 3, which is d.'),
    ('length n is below the limit in Eq.', None),
    ('memory consumption is reduced.', None),
    ('length n is below\nTable 1. Results.', None),
    ('length n\t| d.', None),
    ('length n is below\n\nFooter text.', None),
])
def test_issue108_boundaries(pair, text, expected):
    search, neighbor = pair
    neighbor = replace(neighbor, original_text=text)
    assert completion_excerpt(replace(search, allow_page_completion=True), neighbor) == expected


def test_unrelated_lowercase_is_not_forwarded(pair):
    search, neighbor = pair
    neighbor = replace(neighbor, original_text='memory consumption is reduced.')
    reader = retriever((search, neighbor))
    result = supplement_continuations(search, reader)
    assert result.supplemental_chunks == ()
    with pytest.raises(ValueError):
        validate_search_result(replace(result, supplemental_chunks=(neighbor,)))


def test_unknown_link_is_deferred(pair):
    search, neighbor = pair
    neighbor = replace(neighbor, original_text='positions are processed independently.')
    assert completion_excerpt(replace(search, allow_page_completion=True), neighbor) is None


def test_supplement_cannot_be_used_as_recursive_anchor(pair):
    search, neighbor = pair
    result = supplement_continuations(search, retriever(pair))
    with pytest.raises(ValueError, match='다시 확장'):
        supplement_continuations(result, retriever(pair))
    third = replace(neighbor, chunk_id='c', chunk_index=12, pdf_page=8)
    assert completion_excerpt(result, third) is None


@pytest.mark.parametrize('left,right', [(None, None), (None, 'method'), ('method', None)])
def test_outside_completion_requires_both_section_ids(pair, left, right):
    search, neighbor = pair
    item, = search.items
    search = replace(search, items=(replace(item, chunk=replace(item.chunk, section_id=left)),))
    assert not supplement_continuations(search, retriever((search, replace(neighbor, section_id=right)))).supplemental_chunks


def test_disjoint_pages_only_allow_completion_after_last_page(pair):
    search, neighbor = pair
    search = replace(search, scope=replace(search.scope, pdf_pages=(6, 9)))
    assert not supplement_continuations(search, retriever((search, neighbor))).supplemental_chunks


@pytest.mark.parametrize('boundary', ['etc. ', 'etc.” ', 'etc.'])
def test_ambiguous_abbreviation_blocks_outside_candidate_and_citation(pair, boundary):
    search, neighbor = pair
    unrelated = 'The model stores passwords.'
    neighbor = replace(neighbor, original_text='length is bounded in NLP, MT, ' + boundary + unrelated)
    result = supplement_continuations(search, retriever((search, neighbor)))
    assert not result.supplemental_chunks
    # Recheck at citation validation even if a caller injects the rejected candidate.
    injected = replace(result, supplemental_chunks=(neighbor,))
    citation = Citation('ev', neighbor.chunk_id, 'v', 'p', 't', None, 7, None, unrelated)
    with pytest.raises(ValueError):
        validate_citation(citation, injected)
