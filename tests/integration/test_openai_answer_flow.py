"""OpenAI 통신만 대체하고 검색·생성 변환·근거 저장을 함께 확인한다."""

from contextlib import closing
import json
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.evidence_qa.answer import AnswerService
from solo_leveling.application.evidence_qa.entry import SearchEntryService
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.generation.evidence_store import SQLiteEvidenceWriter
from solo_leveling.infrastructure.generation.openai_generator import OpenAIClaimGenerator, OpenAIGenerationSettings
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteContextReader
from tests.integration.test_scoped_search import setup_search


@pytest.mark.parametrize('valid_reference', [True, False])
def test_openai_response_to_persisted_evidence(setup_search, monkeypatch, valid_reference):
    db, _, retriever, _, _ = setup_search

    def post(url, **kwargs):
        supplied = json.loads(kwargs['json']['input'][0]['content'])['evidence']
        eid = supplied[0]['evidence_id'] if valid_reference else 'not-supplied'
        body = json.dumps({'claims': [{'text': '근거 기반 주장', 'evidence_ids': [eid]}]})
        response = Mock(status_code=200)
        response.json.return_value = {'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': body}]},
        ]}
        return response

    monkeypatch.setattr(requests, 'post', post)
    generator = OpenAIClaimGenerator(OpenAIGenerationSettings('test-key', allow_external_api=True))
    service = AnswerService(SearchEntryService(SQLiteContextReader(db), retriever),
                            generator, SQLiteEvidenceWriter(db))
    answer = service.answer('어텐션이란?', version_id='v')
    if valid_reference:
        assert answer.result.status.value == 'ok'
        ids = [citation.evidence_id for citation in answer.result.citations]
        assert len(repo.get_evidences(db, answer.context_id, ids).evidence) == 1
    else:
        assert answer.result.reason_code.value == 'verification_failed'
        with closing(get_connection(db)) as conn:
            assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0
