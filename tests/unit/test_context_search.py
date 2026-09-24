"""검색 서비스가 학습 맥락의 범위를 벗어난 결과를 거부하는지 검증한다."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.search import ContextSearchService
from solo_leveling.domain.evidence_qa import RetrievedChunk, RetrievalMethod, SearchResult, SearchScope
from solo_leveling.domain.models import Chunk, LearningContext


@pytest.fixture
def parts():
    context = LearningContext('ctx', 'v', 'p', 't', embedding_set_id='idx')
    contexts = SimpleNamespace(get_context=lambda key: context if key == 'ctx' else None)
    calls = []

    def search(question, scope, top_k):
        calls.append((question, scope, top_k))
        return SearchResult(question, scope, RetrievalMethod.VECTOR, 'idx', ())

    retriever = SimpleNamespace(search=search)
    return context, contexts, retriever, calls


def test_resolves_context_and_filters(parts):
    _, contexts, retriever, calls = parts
    result = ContextSearchService(contexts, retriever).search('어텐션이란?', 'ctx', 3,
                                                           pdf_pages=(2,), section_ids=('intro',))
    assert result.scope == SearchScope('v', 'p', 't', (2,), ('intro',))
    assert calls == [('어텐션이란?', result.scope, 3)]


@pytest.mark.parametrize('question,context_id,top_k', [(' ', 'ctx', 1), ('질문', ' ', 1),
    ('질문', 'ctx', 0), ('질문', 'ctx', True), ('질문', 'missing', 1)])
def test_invalid_request_never_searches(parts, question, context_id, top_k):
    _, contexts, retriever, calls = parts
    with pytest.raises(ValueError):
        ContextSearchService(contexts, retriever).search(question, context_id, top_k)
    assert not calls


@pytest.mark.parametrize('field', ['embedding_set_id', 'translation_revision_id'])
def test_context_requires_pinned_translation_and_index(parts, field):
    context, contexts, retriever, calls = parts
    contexts.get_context = lambda key: replace(context, **{field: None})
    with pytest.raises(ValueError):
        ContextSearchService(contexts, retriever).search('질문', 'ctx')
    assert not calls


@pytest.mark.parametrize('case', ['index', 'scope', 'query', 'duplicate', 'rank', 'page', 'count'])
def test_rejects_invalid_adapter_results(parts, case):
    _, contexts, retriever, _ = parts

    def search(question, scope, top_k):
        chunk = Chunk('c', 'p', 0, 'Original', '번역', 't', pdf_page=1)
        items = (RetrievedChunk(chunk, 0.9, 1),)
        if case == 'duplicate':
            items += (RetrievedChunk(chunk, 0.8, 2),)
        if case == 'count':
            items += (RetrievedChunk(replace(chunk, chunk_id='other'), 0.8, 2),)
        if case == 'rank':
            items = (RetrievedChunk(chunk, 0.9, 2),)
        return SearchResult('다른 질문' if case == 'query' else question,
            replace(scope, version_id='other') if case == 'scope' else scope,
            RetrievalMethod.VECTOR, 'other' if case == 'index' else 'idx', items)

    retriever.search = search
    with pytest.raises(ValueError):
        ContextSearchService(contexts, retriever).search('질문', 'ctx', 1 if case == 'count' else 5,
                                                       pdf_pages=(2,) if case == 'page' else ())
