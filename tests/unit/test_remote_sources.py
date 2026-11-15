from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteSearchBinding, RemoteSearchFilters, RemoteSearchHit, RemoteRetrievalContractError, validate_remote_hits,
)
from solo_leveling.application.evidence_qa.remote_sources import (
    RemoteSourceChunk, assemble_remote_sources, supplement_remote_sources,
)
from solo_leveling.application.evidence_qa.page_completion import completion_excerpt
from solo_leveling.application.evidence_qa.validators import validate_citation
from solo_leveling.domain.evidence_qa import Citation


@pytest.fixture
def setup():
    b = RemoteSearchBinding('alice', 'ctx', 'p', 'v', 'pub-old', 'parse', 'tr', 'set', 'fp')
    filters = RemoteSearchFilters((6,), ('s4',))
    hits = validate_remote_hits(b, filters, (RemoteSearchHit(b, 'a', 6, 's4', 0),), 5)
    source = RemoteSourceChunk(b, 'a', 0, 6, 's4', 'A method improves when the sequence', '저장 번역\r\n원문 아님')
    return b, filters, hits, source


def test_exact_saved_text_and_old_publication_are_preserved(setup):
    b, f, hits, source = setup
    result = assemble_remote_sources('q', b, f, hits, NS(read_chunks=lambda *_: (source,)))
    assert result.binding.publication_id == 'pub-old'
    assert result.search.items[0].chunk.text == source.translation_text
    assert result.search.items[0].chunk.original_text == source.original_text
    assert result.search.items[0].chunk is not source


@pytest.mark.parametrize('change', ['owner', 'revision', 'page', 'missing', 'extra', 'duplicate', 'no-translation'])
def test_bad_or_incomplete_source_is_not_replaced_by_latest(setup, change):
    b, f, hits, source = setup
    rows = (source,)
    if change == 'owner': rows = (replace(source, binding=replace(b, owner_user_id='bob')),)
    elif change == 'revision': rows = (replace(source, binding=replace(b, translation_revision_id='new')),)
    elif change == 'page': rows = (replace(source, pdf_page=7),)
    elif change == 'missing': rows = ()
    elif change == 'extra': rows = (source, replace(source, chunk_id='unknown'))
    elif change == 'duplicate': rows = (source, source)
    else: rows = (replace(source, translation_text=None),)
    with pytest.raises(RemoteRetrievalContractError):
        assemble_remote_sources('q', b, f, hits, NS(read_chunks=lambda *_: rows))


@pytest.mark.parametrize('strict', [False, True])
def test_legacy_completion_rules_keep_only_first_sentence_and_strict_scope(setup, strict):
    b, f, hits, source = setup
    f = replace(f, strict_pages=strict)
    tail = RemoteSourceChunk(b, 'b', 1, 7, 's4', 'length n is below d.Other unrelated claim.', '저장 번역, 정렬 미확인')
    reader = NS(read_chunks=lambda *_: (source,), following_chunks=lambda *_: (tail,))
    pinned = assemble_remote_sources('q', b, f, hits, reader)
    if strict:
        # 정상 인접 행 중 strict 범위 밖은 제외하고 원래 검색 결과는 유지한다.
        assert supplement_remote_sources(pinned, f, reader).search.supplemental_chunks == ()
    else:
        result = supplement_remote_sources(pinned, f, reader)
        excerpt = completion_excerpt(result.search, result.search.supplemental_chunks[0])
        assert excerpt == 'length n is below d.'
        citation = Citation('e', 'b', 'v', 'parse', 'tr', None, 7, None, excerpt)
        validate_citation(citation, result.search)
        with pytest.raises(ValueError):
            validate_citation(replace(citation, quote_ko=tail.translation_text), result.search)
        assert tail.original_text.endswith('Other unrelated claim.')


def test_foreign_continuation_is_rejected_before_legacy_conversion(setup):
    b, f, hits, source = setup
    reader = NS(read_chunks=lambda *_: (source,), following_chunks=lambda *_: (
        RemoteSourceChunk(replace(b, publication_id='new'), 'b', 1, 7, 's4', 'length n is below d.', None),))
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_SOURCE_SCOPE_MISMATCH'):
        supplement_remote_sources(assemble_remote_sources('q', b, f, hits, reader), f, reader)


def test_source_failure_is_fixed_error_without_private_text(setup):
    b, f, hits, _ = setup
    def failed(*_): raise RuntimeError('private-original-and-path')
    with pytest.raises(RemoteRetrievalContractError, match='^REMOTE_SOURCE_UNAVAILABLE$'):
        assemble_remote_sources('q', b, f, hits, NS(read_chunks=failed))


def test_neighbor_from_other_section_is_omitted_without_losing_hit(setup):
    b,f,hits,source=setup
    tail=RemoteSourceChunk(b,'b',1,6,'outside','A different section.',None)
    reader=NS(read_chunks=lambda *_:(source,),following_chunks=lambda *_:(tail,))
    result=supplement_remote_sources(assemble_remote_sources('q',b,f,hits,reader),f,reader)
    assert result.search.supplemental_chunks == ()
    assert result.search.items[0].chunk.chunk_id == 'a'
