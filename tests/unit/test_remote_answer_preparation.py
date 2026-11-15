from dataclasses import replace
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from solo_leveling.application.evidence_qa.remote_answer import RemoteAnswerPreparationService
from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteSearchBinding, RemoteSearchFilters, RemoteSearchHit, RemoteRetrievalContractError,
)
from solo_leveling.application.evidence_qa.remote_search import RemoteContextSearchService
from solo_leveling.application.evidence_qa.remote_sources import RemoteSourceChunk
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim, ClaimSupport
from solo_leveling.domain.semantic_verification import SemanticVerdict as V, SemanticReason as R
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.generation.fake_semantic_verifier import FakeSemanticVerifier


@pytest.fixture
def parts():
    fp = dict(model_id='m', model_revision='r', tokenizer_id='t', tokenizer_revision='r', pooling='mean',
              normalization='l2', max_length=128, dimension=2, distance='cosine', runtime='pytorch',
              runtime_version='v', precision='fp32', artifact_sha256='a'*64)
    binding = RemoteSearchBinding('alice', 'ctx', 'paper', 'v', 'old-pub', 'p', 't', 'set', json.dumps(fp))
    source = RemoteSourceChunk(binding, 'c1', 0, 6, 's4', 'A complete sentence.', '완전한 설명이다.')
    reader = NS(read_chunks=lambda b, ids: (source,), following_chunks=lambda *_: ())
    resolve = Mock(return_value=binding)
    retriever = NS(search=Mock(side_effect=lambda q, b, f, k: (RemoteSearchHit(b, 'c1', 6, 's4', 0),)))
    search = RemoteContextSearchService(resolve, retriever)
    return binding, source, reader, resolve, retriever, search


def test_existing_validation_runs_but_evidence_is_only_buffered(parts):
    b, source, reader, _, _, search = parts
    semantic = FakeSemanticVerifier(((V.SUPPORTED, R.FULL_SUPPORT),))
    prepared = RemoteAnswerPreparationService(search, reader, FakeClaimGenerator(),
        semantic_verification=SemanticVerificationService(semantic)).prepare(
            '설명은?', 'ctx', filters=RemoteSearchFilters((6,), ('s4',)), top_k=2)
    assert prepared.persistence_status == 'not_saved' and prepared.binding == b
    assert prepared.response.result.status.value == 'ok'
    assert prepared.response.verification_level.value == 'model_checked'
    assert prepared.response.verification_history['status'] != 'saved'
    assert prepared.evidence[0].quote_original == source.original_text
    assert prepared.evidence[0].quote_ko == source.translation_text
    assert prepared.evidence[0].evidence_id == prepared.response.result.citations[0].evidence_id
    assert len(semantic.calls) == 1


def test_page_completion_exposes_only_allowed_original_sentence_to_generator(parts):
    b, source, reader, _, _, search = parts
    head = replace(source, original_text='The method improves when the sequence', translation_text='방법은')
    tail = RemoteSourceChunk(b, 'c2', 1, 7, 's4',
        'length n is below d.Other unrelated claim.', '정렬되지 않은 번역과 다른 내용')
    reader.read_chunks = lambda *_: (head,)
    reader.following_chunks = lambda *_: (tail,)
    seen = []
    def generate(question, evidence):
        seen.extend(evidence)
        return GeneratedAnswerDraft((GeneratedClaim('n이 d보다 작을 때 개선된다.',
            tuple(e.evidence_id for e in evidence),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in evidence)),))
    result = RemoteAnswerPreparationService(search, reader, NS(generate_claims=generate)).prepare(
        '개선 조건은?', 'ctx', filters=RemoteSearchFilters((6,), ('s4',)))
    assert seen[1].original_text == 'length n is below d.' and seen[1].text_ko is None
    assert seen[1].follows_evidence_id == seen[0].evidence_id
    assert {e.chunk_id for e in result.evidence} == {'c1', 'c2'}
    assert len(result.response.result.claims) == 1
    assert all('unrelated' not in (e.quote_original or '') for e in result.evidence)
    assert all(e.quote_ko is None for e in result.evidence if e.chunk_id == 'c2')
    assert tail.original_text.endswith('Other unrelated claim.')


@pytest.mark.parametrize('mode', ['owner', 'revision', 'missing', 'deleted'])
def test_source_or_access_failure_precedes_generation(parts, mode):
    b, source, reader, resolve, _, search = parts
    gen = NS(generate_claims=Mock(side_effect=AssertionError('must not generate')))
    if mode == 'deleted': resolve.return_value = None
    elif mode == 'missing': reader.read_chunks = lambda *_: ()
    else:
        foreign = replace(b, **({'owner_user_id': 'bob'} if mode == 'owner' else {'translation_revision_id': 'new'}))
        reader.read_chunks = lambda *_: (replace(source, binding=foreign),)
    with pytest.raises(RemoteRetrievalContractError):
        RemoteAnswerPreparationService(search, reader, gen).prepare('q', 'ctx')
    gen.generate_claims.assert_not_called()


def test_strict_pages_still_rejects_outside_reader_candidate(parts):
    b, source, reader, _, _, search = parts
    reader.read_chunks = lambda *_: (replace(source, original_text='Faster when the sequence'),)
    reader.following_chunks = lambda *_: (RemoteSourceChunk(b, 'c2', 1, 7, 's4', 'length is small.', '번역'),)
    gen = NS(generate_claims=Mock())
    with pytest.raises(ValueError):
        RemoteAnswerPreparationService(search, reader, gen).prepare('q', 'ctx',
            filters=RemoteSearchFilters((6,), ('s4',), True))
    gen.generate_claims.assert_not_called()


def test_search_expansion_cannot_switch_publication(parts):
    b, source, reader, resolve, _, search = parts
    resolve.side_effect = [b, replace(b, publication_id='new-pub')]
    gen = NS(generate_claims=Mock(return_value=GeneratedAnswerDraft(())))
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_SCOPE_MISMATCH'):
        RemoteAnswerPreparationService(search, reader, gen).prepare('q', 'ctx', top_k=1)
    assert resolve.call_count == 2 and gen.generate_claims.call_count == 1


def test_invalid_generated_reference_is_not_buffered(parts):
    _, _, reader, _, _, search = parts
    gen = NS(generate_claims=lambda *_: GeneratedAnswerDraft((GeneratedClaim('주장', ('foreign-evidence',)),)))
    result = RemoteAnswerPreparationService(search, reader, gen).prepare('q', 'ctx')
    assert result.response.result.status.value == 'insufficient_evidence'
    assert result.response.result.claims == () and result.evidence == ()


def test_semantically_rejected_claim_is_not_buffered(parts):
    _, _, reader, _, _, search = parts
    verifier = FakeSemanticVerifier(((V.CONTRADICTED, R.CONTRADICTION),))
    result = RemoteAnswerPreparationService(search, reader, FakeClaimGenerator(),
        semantic_verification=SemanticVerificationService(verifier)).prepare('설명은?', 'ctx')
    assert len(verifier.calls) == 1
    assert result.response.result.claims == () and result.evidence == ()
    assert result.response.result.status.value == 'insufficient_evidence'


def test_empty_search_does_not_read_sources_or_generate(parts):
    _, _, reader, _, retriever, search = parts
    retriever.search.side_effect = None
    retriever.search.return_value = ()
    reader.read_chunks = Mock(side_effect=AssertionError('must not read'))
    gen = NS(generate_claims=Mock(side_effect=AssertionError('must not generate')))
    result = RemoteAnswerPreparationService(search, reader, gen).prepare('q', 'ctx')
    assert result.evidence == () and result.response.result.status.value == 'insufficient_evidence'
    assert result.persistence_status == 'not_saved'
