"""근거 부족 시 추가 검색의 상한·범위·저장 정책을 검증한다."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerGenerationError, AnswerService
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
from solo_leveling.domain.evidence_qa import (
    GeneratedAnswerDraft, GeneratedClaim, RetrievedChunk, RetrievalMethod, SearchResult, SearchScope,
)
from solo_leveling.domain.models import Chunk


@pytest.fixture
def setup():
    items = tuple(RetrievedChunk(Chunk(str(i), 'p', i, f'Original {i}', f'근거 {i}', 't',
        pdf_page=1, section_id='method'), 1.0, i + 1) for i in range(10))
    first = SearchResult('질문', SearchScope('v', 'p', 't', (1,), ('method',)),
                         RetrievalMethod.VECTOR, 'idx', items[:5])
    second = replace(first, items=items)
    search = Mock(side_effect=[ContextSearchResponse('ctx', first), ContextSearchResponse('ctx', second)])
    writer = Mock()
    inputs = []

    def generate(question, evidence):
        inputs.append(evidence)
        if len(inputs) == 1:
            return GeneratedAnswerDraft(())
        return GeneratedAnswerDraft((GeneratedClaim('추가 근거 답변', (evidence[-1].evidence_id,)),))

    generator = Mock(side_effect=generate)
    service = AnswerService(SimpleNamespace(search=search), SimpleNamespace(generate_claims=generator), writer)
    return service, search, generator, writer, first, second, inputs


def test_expands_once_with_same_context_and_scope_and_saves_only_final(setup):
    service, search, generator, writer, first, second, inputs = setup
    response = service.answer('질문', version_id='v', pdf_pages=(1,), section_ids=('method',))
    assert [call.kwargs['top_k'] for call in search.call_args_list] == [5, 10]
    assert search.call_args_list[1].kwargs == dict(context_id='ctx', version_id='v', top_k=10,
                                                 pdf_pages=(1,), section_ids=('method',))
    assert generator.call_count == 2
    assert response.search == second
    assert response.result.status.value == 'ok'
    assert response.result.citations[0].chunk_id == '9'
    writer.save.assert_called_once()
    assert len(writer.save.call_args.args[1]) == 1
    assert not {e.evidence_id for e in inputs[0]} & {e.evidence_id for e in inputs[1]}


def test_second_empty_draft_stops_without_saving(setup):
    service, search, generator, writer, *_ = setup
    generator.side_effect = lambda *a: GeneratedAnswerDraft(())
    response = service.answer('질문')
    assert response.result.reason_code.value == 'evidence_not_found'
    assert search.call_count == generator.call_count == 2
    writer.save.assert_not_called()


def test_same_candidates_do_not_trigger_second_generation(setup):
    service, search, generator, writer, first, *_ = setup
    search.side_effect = [ContextSearchResponse('ctx', first)] * 2
    response = service.answer('질문')
    assert response.search == first
    assert search.call_count == 2 and generator.call_count == 1
    writer.save.assert_not_called()


def test_exhausted_first_search_skips_expansion(setup):
    service, search, generator, _, first, *_ = setup
    search.side_effect = [ContextSearchResponse('ctx', replace(first, items=first.items[:2]))]
    service.answer('질문')
    assert search.call_count == generator.call_count == 1


def test_expansion_cap(setup):
    service, search, _, _, first, second, _ = setup
    service.max_expanded_top_k = 7
    search.side_effect = [ContextSearchResponse('ctx', first),
                          ContextSearchResponse('ctx', replace(second, items=second.items[:7]))]
    service.answer('질문')
    assert search.call_args.kwargs['top_k'] == 7


def test_no_expansion_at_cap(setup):
    service, search, generator, *_ = setup
    service.max_expanded_top_k = 5
    service.answer('질문')
    assert search.call_count == generator.call_count == 1


@pytest.mark.parametrize('failure', ['format', 'unknown', 'unavailable'])
def test_second_generation_failure_never_saves_first_attempt(setup, failure):
    service, search, generator, writer, *_ = setup
    bad = (GenerationFormatError('형식 오류') if failure == 'format' else
           GenerationUnavailable('장애') if failure == 'unavailable' else
           GeneratedAnswerDraft((GeneratedClaim('주장', ('unknown',)),)))
    generator.side_effect = [GeneratedAnswerDraft(()), bad]
    if failure == 'unavailable':
        with pytest.raises(AnswerGenerationError):
            service.answer('질문')
    else:
        assert service.answer('질문').result.reason_code.value == 'verification_failed'
    assert search.call_count == generator.call_count == 2
    writer.save.assert_not_called()


@pytest.mark.parametrize('failure', ['format', 'unknown', 'unavailable'])
def test_failures_are_not_evidence_shortage(setup, failure):
    service, search, generator, writer, *_ = setup
    if failure == 'format':
        generator.side_effect = GenerationFormatError('형식 오류')
    elif failure == 'unknown':
        generator.side_effect = lambda *a: GeneratedAnswerDraft((GeneratedClaim('주장', ('unknown',)),))
    else:
        generator.side_effect = GenerationUnavailable('장애')
    if failure == 'unavailable':
        with pytest.raises(AnswerGenerationError):
            service.answer('질문')
    else:
        assert service.answer('질문').result.reason_code.value == 'verification_failed'
    assert search.call_count == generator.call_count == 1
    writer.save.assert_not_called()


@pytest.mark.parametrize('change', ['context', 'index', 'scope', 'missing', 'changed'])
def test_expansion_cannot_change_provenance(setup, change):
    service, search, generator, writer, first, second, _ = setup
    context = 'other' if change == 'context' else 'ctx'
    if change == 'index':
        second = replace(second, embedding_set_id='other')
    elif change == 'scope':
        second = replace(second, scope=replace(second.scope, pdf_pages=()))
    elif change == 'missing':
        second = replace(second, items=tuple(replace(item, rank=i+1) for i, item in enumerate(second.items[1:])))
    elif change == 'changed':
        second = replace(second, items=(replace(second.items[0], chunk=replace(second.items[0].chunk, text='변경')),
                                        *second.items[1:]))
    search.side_effect = [ContextSearchResponse('ctx', first), ContextSearchResponse(context, second)]
    with pytest.raises(ValueError):
        service.answer('질문')
    assert generator.call_count == 1
    writer.save.assert_not_called()


@pytest.mark.parametrize('limit', [0, True, -1])
def test_invalid_limit(limit):
    with pytest.raises(ValueError):
        AnswerService(None, None, None, max_expanded_top_k=limit)
