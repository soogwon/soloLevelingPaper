"""답변 서비스와 게시된 임시 SQLite 색인의 검증 이력 연결을 확인한다."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.evidence_qa import RetrievedChunk, RetrievalMethod, SearchResult, SearchScope
from solo_leveling.domain.semantic_verification import SemanticReason as R, SemanticVerdict as V
from solo_leveling.domain.verification_history import chunk_content_hash
from solo_leveling.infrastructure.database import repository
from solo_leveling.infrastructure.database.verification_history import list_verification_history
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceWriter
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.generation.fake_semantic_verifier import FakeSemanticVerifier
from tests.unit.test_learning_persistence import publish, setup_db


@pytest.fixture
def configured(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    rows = repository.get_translated_chunks(db, 'p1', 't1')
    search = SearchResult('질문', SearchScope('v1', 'p1', 't1'), RetrievalMethod.VECTOR, 'idx',
                          tuple(RetrievedChunk(c, 1., i) for i, c in enumerate(rows, 1)))
    writer = SQLiteEvidenceWriter(db)
    service = AnswerService(SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', search)),
                            FakeClaimGenerator(), writer, save_history=writer.save_history)
    return db, service


@pytest.mark.parametrize('mode,expected', [
    ('disabled', 'ok'), ('mixed', 'partial'), ('excluded', 'insufficient_evidence'),
    ('invalid', 'insufficient_evidence'), ('error', 'ok'), ('mismatch', 'ok'),
])
def test_answer_history(configured, mode, expected):
    db, service = configured
    supported = (V.SUPPORTED, R.FULL_SUPPORT)
    excluded = (V.CONTRADICTED, R.CONTRADICTION)
    outcomes = {'mixed': [supported, excluded], 'excluded': [excluded, excluded], 'invalid': []}
    if mode != 'disabled':
        service.semantic_verification = SemanticVerificationService(
            FakeSemanticVerifier(outcomes.get(mode, [supported, supported])))
    save = service.save_history
    if mode == 'error':
        def unavailable(record):
            raise RuntimeError('비공개 오류')
        service.save_history = unavailable
    if mode == 'mismatch':
        service.save_history = lambda record: save(replace(record, embedding_set_id='wrong'))
    response = service.answer('질문', context_id='ctx')
    assert response.result.status.value == expected
    history = list_verification_history(db, 'ctx')
    if mode in ('disabled', 'error', 'mismatch'):
        assert response.verification_history == {'status': 'not_used' if mode == 'disabled' else 'failed'}
        assert not history and len(response.result.citations) == 2
        return
    assert response.verification_history == {'status': 'saved'}
    record, = history
    if mode == 'invalid':
        assert record.failure and not record.claims and record.level == 'structural_only'
    else:
        assert record.level == 'model_checked' and record.failure is None
        kept = {c.claim_id for c in response.result.claims}
        assert len(record.claims) == 2
        assert [c.retained for c in record.claims] == [mode == 'mixed', False]
        assert all(c.retained == (c.claim_id in kept) for c in record.claims)
        assert record.claims[1].references[0].content_hash == chunk_content_hash('Second.')
    original = replace(record, created_at=None)
    assert save(original)
    assert not save(replace(original, elapsed_ms=original.elapsed_ms + 1))
    assert list_verification_history(db, 'ctx') == history
