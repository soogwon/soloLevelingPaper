"""다음 청크 보충의 경계와 생성·인용 연결을 검증한다."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.continuation import supplement_continuations
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.domain.evidence_qa import (
    ClaimSupport, GeneratedAnswerDraft, GeneratedClaim, RetrievedChunk, RetrievalMethod, SearchResult, SearchScope,
)
from solo_leveling.domain.models import Chunk


def sample():
    first = Chunk('a', 'p', 10, 'Faster when the sequence', '시퀀스가', 't', pdf_page=6)
    following = Chunk('b', 'p', 11, 'length is smaller than the dimension.', '길이가 차원보다 작을 때.', 't', pdf_page=7)
    search = SearchResult('질문', SearchScope('v', 'p', 't', (6, 7)),
                          RetrievalMethod.VECTOR, 'idx', (RetrievedChunk(first, .9, 1),))
    return search, following


@pytest.mark.parametrize('page', [6, 7])
def test_cross_page_keeps_search_rank_and_own_identity(page):
    search, following = sample()
    following = replace(following, pdf_page=page)
    calls = []
    def read(result, indices):
        calls.append(indices)
        return (following,)
    result = supplement_continuations(search, SimpleNamespace(following_chunks=read))
    assert calls == [(11,)]
    assert result.items == search.items
    assert result.supplemental_chunks == (following,)
    assert result.supplemental_chunks[0] is not following
    assert result.candidate_chunks[1].pdf_page == page


@pytest.mark.parametrize('change', [
    {'pdf_page': 8}, {'parse_revision_id': 'other'}, {'translation_revision_id': 'other'},
    {'text': None}, {'chunk_index': 12},
])
def test_invalid_reader_result_is_rejected(change):
    search, following = sample()
    reader = SimpleNamespace(following_chunks=lambda *a: (replace(following, **change),))
    with pytest.raises(ValueError):
        supplement_continuations(search, reader)


def test_complete_sentence_does_not_read():
    search, _ = sample()
    search = replace(search, items=(replace(search.items[0],
        chunk=replace(search.items[0].chunk, original_text='Complete.')),))
    reader = SimpleNamespace(following_chunks=lambda *a: pytest.fail('조회하면 안 됨'))
    assert supplement_continuations(search, reader) == search


@pytest.mark.parametrize('following,expected', [
    ('of the paths forward and backward signals have to traverse in the network. '
     'The shorter these paths, the easier it is to learn dependencies.', True),
    ('An unrelated complete sentence.', False),
    ('of the paths forward and backward signals have to traverse in the network.', False),
    ('of the paths forward and backward signals have to traverse in the network. Unfinished', False),
])
def test_complete_anchor_adds_only_overlapping_new_sentence(following, expected):
    search, next_chunk = sample()
    original = ('One key factor is the length of the paths forward and backward signals '
                'have to traverse in the network.')
    search = replace(search, items=(replace(search.items[0],
        chunk=replace(search.items[0].chunk, original_text=original)),))
    next_chunk = replace(next_chunk, original_text=following)
    result = supplement_continuations(search,
        SimpleNamespace(following_chunks=lambda *a: (next_chunk,)),
        include_overlapping_neighbors=True)
    assert bool(result.supplemental_chunks) is expected
    assert result.items == search.items


def test_path_direction_uses_own_neighbor_citation_not_anchor_only():
    search, next_chunk = sample()
    original = ('One key factor affecting the ability to learn such dependencies is the length '
                'of the paths forward and backward signals have to traverse in the network.')
    direction = ('The shorter these paths between any combination of positions in the input '
                 'and output sequences, the easier it is to learn long-range dependencies [12].')
    search = replace(search, items=(replace(search.items[0],
        chunk=replace(search.items[0].chunk, original_text=original)),))
    next_chunk = replace(next_chunk, original_text=(
        'of the paths forward and backward signals have to traverse in the network. ' + direction))
    saved = []

    def generate(question, evidence):
        assert len(evidence) == 2
        neighbor = evidence[1]
        # 완결 청크 뒤의 문맥 후보를 미완결 문장의 연속으로 표시하지 않는다.
        assert neighbor.follows_evidence_id is None
        return GeneratedAnswerDraft((GeneratedClaim('경로가 짧을수록 학습하기 쉽다.',
            (neighbor.evidence_id,), (ClaimSupport(neighbor.evidence_id, direction),)),))

    service = AnswerService(
        SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', search)),
        SimpleNamespace(generate_claims=generate),
        SimpleNamespace(save=lambda ctx, evidence: saved.extend(evidence)),
        continuation_reader=SimpleNamespace(following_chunks=lambda *a: (next_chunk,)))
    response = service.answer('경로 길이의 영향은?', context_id='ctx', pdf_pages=(6, 7))
    assert response.result.status.value == 'ok'
    assert [(c.chunk_id, c.pdf_page) for c in response.result.citations] == [('b', 7)]
    assert saved[0].quote_original == next_chunk.original_text


def test_overlapping_neighbors_still_validate_scope():
    search, following = sample()
    search = replace(search, scope=replace(search.scope, pdf_pages=(6,)),
        items=(replace(search.items[0], chunk=replace(search.items[0].chunk,
            original_text='The signals traverse in the network.')),))
    following = replace(following, original_text='The signals traverse in the network. More context.')
    with pytest.raises(ValueError):
        supplement_continuations(search, SimpleNamespace(following_chunks=lambda *a: (following,)),
                                 include_overlapping_neighbors=True)


def test_unfinished_neighbor_has_priority_over_overlap_with_limit():
    search, following = sample()
    complete = replace(search.items[0].chunk, chunk_id='c', chunk_index=20,
                       original_text='The signals traverse in the network.')
    search = replace(search, items=(RetrievedChunk(complete, .95, 1),
                                    replace(search.items[0], rank=2)))
    extra = replace(following, chunk_id='d', chunk_index=21,
                    original_text='The signals traverse in the network. More context.')
    calls = []
    def read(result, indices):
        calls.append(indices)
        return (following, extra)
    result = supplement_continuations(search, SimpleNamespace(following_chunks=read),
                                     limit=1, include_overlapping_neighbors=True)
    assert calls == [(11, 21)]
    assert result.supplemental_chunks == (following,)


def test_missing_next_is_not_skipped_and_existing_next_is_not_duplicated():
    search, following = sample()
    reader = SimpleNamespace(following_chunks=lambda *a: ())
    assert not supplement_continuations(search, reader).supplemental_chunks
    search = replace(search, items=search.items + (RetrievedChunk(following, .8, 2),))
    reader.following_chunks = lambda *a: (following,)
    assert not supplement_continuations(search, reader).supplemental_chunks


def test_no_recursive_expansion_and_limit():
    search, following = sample()
    second = replace(search.items[0].chunk, chunk_id='c', chunk_index=20)
    search = replace(search, items=search.items + (RetrievedChunk(second, .8, 2),))
    calls = []
    def read(result, indices):
        calls.append(indices)
        return (replace(following, original_text='still incomplete'),
                replace(following, chunk_id='d', chunk_index=21))
    result = supplement_continuations(search, SimpleNamespace(following_chunks=read), limit=1)
    assert calls == [(11, 21)]
    assert len(result.supplemental_chunks) == 1


def test_supplemental_citation_and_save_keep_page_and_ids():
    search, following = sample()
    saved = []
    def generate(question, evidence):
        assert len(evidence) == 2
        assert evidence[1].follows_evidence_id == evidence[0].evidence_id
        return GeneratedAnswerDraft((GeneratedClaim('조건부 비교', tuple(e.evidence_id for e in evidence)),))
    service = AnswerService(
        SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', search)),
        SimpleNamespace(generate_claims=generate),
        SimpleNamespace(save=lambda ctx, evidence: saved.extend(evidence)),
        continuation_reader=SimpleNamespace(following_chunks=lambda *a: (following,)))
    response = service.answer('질문', context_id='ctx', top_k=1)
    assert [(c.chunk_id, c.pdf_page) for c in response.result.citations] == [('a', 6), ('b', 7)]
    assert len({c.evidence_id for c in response.result.citations}) == 2
    assert [e.chunk_id for e in saved] == ['a', 'b']
    assert len(response.search.items) == 1


def test_supplements_do_not_prevent_top_k_expansion():
    search, following = sample()
    calls = []
    def retrieve(question, **kwargs):
        calls.append(kwargs['top_k'])
        result = search if kwargs['top_k'] == 1 else replace(search,
            items=search.items + (RetrievedChunk(following, .8, 2),))
        return ContextSearchResponse('ctx', result)
    service = AnswerService(SimpleNamespace(search=retrieve),
        SimpleNamespace(generate_claims=lambda *a: GeneratedAnswerDraft(())),
        SimpleNamespace(save=lambda *a: pytest.fail('빈 답변 저장 금지')),
        continuation_reader=SimpleNamespace(following_chunks=lambda *a: (following,)))
    service.answer('질문', context_id='ctx', top_k=1)
    assert calls == [1, 2]
