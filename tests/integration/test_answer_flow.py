"""검색부터 근거의 영속 저장·재조회까지 실제 저장소로 검증한다."""

from contextlib import closing
from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import SearchEntryService
from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceWriter
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader
from tests.integration.test_scoped_search import setup_search


def test_answer_uses_default_context_and_persists_citations(setup_search):
    db, _, retriever, _, _ = setup_search
    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever),
                            FakeClaimGenerator(), SQLiteEvidenceWriter(db))
    response = service.answer('어텐션이란?', version_id='v', top_k=2)
    ids = [c.evidence_id for c in response.result.citations]
    details = repo.get_evidences(db, response.context_id, ids)
    assert len(details.evidence) == 2
    assert details.evidence[0].quote_ko == '어텐션 설명'
    assert details.evidence[0].quote_original == 'Attention.'
    assert details.evidence[0].file_display_name == 'a.pdf'
    assert repo.get_learning_context(db, response.context_id).version_id == 'v'


def test_invalid_generation_never_persists_evidence(setup_search):
    db, _, retriever, _, _ = setup_search
    generator = SimpleNamespace(generate_claims=lambda *args: GeneratedAnswerDraft((GeneratedClaim('주장', ('unknown',)),)))
    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever), generator, SQLiteEvidenceWriter(db))
    response = service.answer('질문', context_id='ctx')
    assert response.result.reason_code.value == 'verification_failed'
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0


def test_changed_quote_causes_atomic_save_rollback(setup_search):
    db, _, retriever, _, _ = setup_search

    def generate(question, evidence):
        # 검색 이후 DB 번역문이 바뀌면 앞선 정상 근거까지 함께 저장 취소해야 한다.
        with closing(get_connection(db)) as conn, conn:
            conn.execute("UPDATE chunks SET text='변경된 번역' WHERE chunk_id='b'")
        return FakeClaimGenerator().generate_claims(question, evidence)

    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever),
        SimpleNamespace(generate_claims=generate), SQLiteEvidenceWriter(db))
    with pytest.raises(ValueError, match='translated quote not found'):
        service.answer('질문', context_id='ctx')
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0
