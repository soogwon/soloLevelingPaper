"""실제 저장소를 거치는 답변에서 단계 로그와 비노출을 확인한다."""

import json

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import SearchEntryService
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceWriter
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader
from tests.integration.test_scoped_search import setup_search


def test_answer_stage_logs(setup_search, capsys):
    db, _, retriever, _, _ = setup_search
    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever),
        FakeClaimGenerator(), SQLiteEvidenceWriter(db))
    capsys.readouterr()
    service.answer('SECRET question', context_id='ctx', top_k=2)
    captured = capsys.readouterr()
    assert captured.out == ''
    assert all(value not in captured.err for value in ('SECRET', 'Attention.', '어텐션', db))
    logs = [json.loads(line) for line in captured.err.splitlines()]
    assert len({r['request_id'] for r in logs}) == 1
    starts = {r['stage'] for r in logs if r['event'] == 'start'}
    assert {'answer', 'context_lookup', 'index_lookup', 'chroma_validation',
            'query_embedding', 'vector_search', 'generation',
            'evidence_validation', 'evidence_save'} <= starts
    for record in logs:
        if record['event'] == 'start':
            assert any(r['span_id'] == record['span_id'] and r['event'] == 'end' for r in logs)
