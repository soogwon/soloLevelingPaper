"""게시 색인에 속한 다음 청크만 사용자 범위 안에서 조회한다."""

from dataclasses import replace
from contextlib import closing
from types import SimpleNamespace

import pytest

from tests.integration.test_scoped_search import setup_search
from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import SearchEntryService
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.semantic_verification import SemanticReason, SemanticVerdict
from solo_leveling.infrastructure.generation.fake_semantic_verifier import FakeSemanticVerifier
from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceWriter
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader


@pytest.mark.parametrize('pages,sections,expected', [
    ((), (), ['b']), ((1, 2), (), ['b']), ((1,), (), []),
    ((), ('intro',), []), ((1, 2), ('method',), ['b']),
    ((1,), ('method',), []),
])
def test_following_chunks_respect_scope(setup_search, pages, sections, expected):
    _, _, retriever, service, calls = setup_search
    search = service.search('질문', 'ctx', top_k=1)
    search = replace(search, scope=replace(search.scope, pdf_pages=pages, section_ids=sections))
    result = retriever.following_chunks(search, (1,))
    assert [c.chunk_id for c in result] == expected
    assert len(calls) == 1


def test_following_chunks_reject_changed_index(setup_search):
    _, _, retriever, service, _ = setup_search
    search = service.search('질문', 'ctx', top_k=1)
    with pytest.raises(ValueError, match='색인'):
        retriever.following_chunks(replace(search, embedding_set_id='other'), (1,))


def test_following_chunks_missing_index_does_not_skip(setup_search):
    _, _, retriever, service, _ = setup_search
    search = service.search('질문', 'ctx', top_k=1)
    assert retriever.following_chunks(search, (99,)) == ()


def test_cross_page_candidate_is_saved_and_read_with_own_page(setup_search):
    db, _, retriever, _, _ = setup_search
    # 임시 DB에서만 원문 경계와 페이지를 재현한다. 번역문·벡터는 유지한다.
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='Faster when the sequence', pdf_page=6 WHERE chunk_id='a'")
        conn.execute("UPDATE chunks SET original_text='length is smaller.', pdf_page=7 WHERE chunk_id='b'")
    def generate(question, evidence):
        assert [e.chunk_id for e in evidence] == ['a', 'b']
        assert evidence[1].follows_evidence_id == evidence[0].evidence_id
        return GeneratedAnswerDraft((GeneratedClaim('테스트 주장', tuple(e.evidence_id for e in evidence)),))
    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever),
        SimpleNamespace(generate_claims=generate), SQLiteEvidenceWriter(db), continuation_reader=retriever)
    response = service.answer('질문', context_id='ctx', top_k=1, pdf_pages=(6, 7))
    ids = [c.evidence_id for c in response.result.citations]
    stored = repo.get_evidences(db, 'ctx', ids)
    assert {(e.chunk_id, e.pdf_page) for e in stored.evidence} == {('a', 6), ('b', 7)}
    assert len(response.search.items) == 1


@pytest.mark.parametrize('strict', [False, True])
def test_page_completion_through_real_stores(setup_search, strict):
    db, _, retriever, _, _ = setup_search
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='Faster when the sequence', pdf_page=6 WHERE chunk_id='a'")
        conn.execute("UPDATE chunks SET original_text='length n is below d.Other claim.', pdf_page=7, section_id='intro' WHERE chunk_id='b'")
    def generate(question, evidence):
        assert [e.chunk_id for e in evidence] == (['a'] if strict else ['a', 'b'])
        if strict:
            return GeneratedAnswerDraft(())
        assert evidence[1].original_text == 'length n is below d.'
        assert evidence[1].text_ko is None
        return GeneratedAnswerDraft((GeneratedClaim('n이 d보다 작을 때 더 빠르다.', tuple(e.evidence_id for e in evidence)),))
    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever),
        SimpleNamespace(generate_claims=generate), SQLiteEvidenceWriter(db), continuation_reader=retriever)
    service.semantic_verification = SemanticVerificationService(FakeSemanticVerifier([
        (SemanticVerdict.SUPPORTED, SemanticReason.FULL_SUPPORT)]))
    service.save_history = SQLiteEvidenceWriter(db).save_history
    response = service.answer('조건은?', context_id='ctx', top_k=1, pdf_pages=(6,), strict_pages=strict)
    assert [i.chunk.pdf_page for i in response.search.items] == [6]
    if not strict:
        assert response.verification_history == {'status': 'saved'}
        stored = repo.get_evidences(db, 'ctx', [c.evidence_id for c in response.result.citations])
        following = next(e for e in stored.evidence if e.chunk_id == 'b')
        assert following.pdf_page == 7
        assert following.quote_original == 'length n is below d.'
        assert following.quote_ko is None
    with closing(get_connection(db)) as conn:
        row = conn.execute("SELECT original_text, text FROM chunks WHERE chunk_id='b'").fetchone()
        assert row['original_text'] == 'length n is below d.Other claim.'
        assert row['text'] == '병렬 처리'
