"""MCP 도구 스키마·응답·안전한 오류 변환을 검증한다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.evidence_qa.answer import AnswerGenerationError
from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Citation, Claim, EvidenceDetail, GetEvidenceResult,
    RetrievalMethod, RetrievedChunk, SearchResult, SearchScope,
)
from solo_leveling.domain.models import Chunk, JobStatus, RequestConflictError
from solo_leveling.interfaces.mcp.errors import UnsupportedDocumentError
from solo_leveling.interfaces.mcp.server import MCPServices, create_server


def run(awaitable):
    return asyncio.run(awaitable)


def payload(result):
    """FastMCP 내부 호출은 콘텐츠 블록과 structuredContent를 함께 반환한다."""
    return result[1]


def answer_response():
    chunk = Chunk('chunk', 'parse', 0, 'Original.', '번역.', 'translation', pdf_page=1)
    scope = SearchScope('version', 'parse', 'translation')
    search = SearchResult('질문', scope, RetrievalMethod.VECTOR, 'embedding',
                          (RetrievedChunk(chunk, 1.0, 1),))
    citation = Citation('evidence', 'chunk', 'version', 'parse', 'translation', None, 1,
                        '번역.', 'Original.')
    result = AnswerResult(AnswerStatus.OK, '답변',
                          (Claim('claim', '주장', ('evidence',)),), (citation,), None)
    return SimpleNamespace(context_id='context', search=search, result=result)


def make_server(answer=None, evidence=None, ingestion=None):
    answer = answer or Mock()
    evidence = evidence or Mock()
    return create_server(MCPServices(answer, evidence, ingestion)), answer, evidence


def test_tools_and_annotations():
    server, _, _ = make_server()
    tools = {tool.name: tool for tool in run(server.list_tools())}
    assert set(tools) == {'ask_paper', 'get_evidence'}
    assert tools['ask_paper'].annotations.readOnlyHint is False
    assert tools['ask_paper'].annotations.openWorldHint is True
    assert tools['get_evidence'].annotations.readOnlyHint is True
    assert tools['get_evidence'].annotations.openWorldHint is False
    assert tools['ask_paper'].outputSchema['required'] == [
        'context_id', 'verification_level', 'status', 'answer_ko',
        'claims', 'citations', 'reason_code',
    ]
    assert tools['ask_paper'].inputSchema['$defs']['Focus']['additionalProperties'] is False


def test_ask_returns_flat_contract_and_focus():
    server, answer, _ = make_server()
    answer.answer.return_value = answer_response()
    result = payload(run(server.call_tool('ask_paper', {'context_id': 'context', 'question': '후속 질문',
        'standalone_question': '독립 질문', 'focus': {'pdf_pages': [1], 'section_ids': ['intro']},
        'top_k': 3})))
    assert result['context_id'] == 'context'
    assert result['status'] == 'ok'
    assert result['verification_level'] == 'structural_only'
    assert 'answer' not in result
    answer.answer.assert_called_once_with('독립 질문', context_id='context', top_k=3,
                                          pdf_pages=(1,), section_ids=('intro',))


def test_get_evidence_returns_stored_details():
    detail = EvidenceDetail('evidence', 'chunk', 'version', 'parse', 'translation', None, 1,
                            '번역.', 'Original.', 'paper.pdf')
    server, _, evidence = make_server()
    evidence.get.return_value = GetEvidenceResult((detail,))
    result = payload(run(server.call_tool('get_evidence',
                                   {'context_id': 'context', 'evidence_ids': ['evidence']})))
    assert result['evidence'][0]['file_display_name'] == 'paper.pdf'
    evidence.get.assert_called_once_with('context', ['evidence'])


@pytest.mark.parametrize(('error', 'code'), [
    (InvalidArgumentError('secret'), 'INVALID_ARGUMENT'),
    (ResourceNotFoundError('secret'), 'NOT_FOUND'),
    (ContextNotReadyError('v', 'job', JobStatus.PROCESSING), 'PAPER_NOT_READY'),
    (AnswerGenerationError('secret'), 'UPSTREAM_UNAVAILABLE'),
    (DataIntegrityError('secret'), 'INTERNAL_ERROR'),
    (RequestConflictError('secret'), 'CONFLICT'),
    (UnsupportedDocumentError('secret'), 'UNSUPPORTED_DOCUMENT'),
    (RuntimeError('secret-path'), 'INTERNAL_ERROR'),
])
def test_safe_error_mapping(error, code):
    answer = Mock()
    answer.answer.side_effect = error
    server, _, _ = make_server(answer=answer)
    with pytest.raises(ToolError) as caught:
        run(server.call_tool('ask_paper', {'context_id': 'context', 'question': '질문'}))
    assert code in str(caught.value)
    assert 'secret' not in str(caught.value)


def test_sdk_rejects_unknown_focus_field():
    server, _, _ = make_server()
    with pytest.raises(Exception):
        run(server.call_tool('ask_paper', {'context_id': 'context', 'question': '질문',
                                           'focus': {'printed_page': '1'}}))


def test_standalone_question_does_not_bypass_original_question_validation():
    server, answer, _ = make_server()
    with pytest.raises(ToolError) as caught:
        run(server.call_tool('ask_paper', {'context_id': 'context', 'question': ' ',
                                           'standalone_question': '독립 질문'}))
    assert 'INVALID_ARGUMENT' in str(caught.value)
    answer.answer.assert_not_called()


def test_registration_tools_and_status_contract():
    ingestion = Mock()
    ingestion.add_local_pdf.return_value = {
        'paper_id': 'paper', 'version_id': 'version', 'job_id': 'job',
        'status': 'processing', 'stage': 'translate', 'limitations': [],
        'result_available': False,
    }
    ingestion.get_status.return_value = {
        'version_id': 'version', 'job_id': 'job', 'status': 'ready', 'stage': 'index',
        'limitations': ['translation_failed:chunk:provider_unavailable'],
        'result_available': True,
    }
    server, _, _ = make_server(ingestion=ingestion)
    tools = {tool.name: tool for tool in run(server.list_tools())}
    assert set(tools) == {'add_paper', 'get_paper_status', 'ask_paper', 'get_evidence'}
    assert tools['add_paper'].annotations.idempotentHint is True
    added = payload(run(server.call_tool('add_paper', {
        'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'},
        'request_key': 'request-1',
    })))
    assert added == {'paper_id': 'paper', 'version_id': 'version',
                     'job_id': 'job', 'status': 'processing'}
    status = payload(run(server.call_tool('get_paper_status', {'job_id': 'job'})))
    assert status['capabilities'] == ['ask_paper', 'get_evidence']
    assert status['limitations'] == ['translation_failed:chunk:provider_unavailable']


def test_nonready_status_has_no_capabilities():
    ingestion = Mock()
    ingestion.get_status.return_value = {
        'version_id': 'version', 'job_id': 'job', 'status': 'processing',
        'stage': 'translate', 'limitations': [], 'result_available': False,
    }
    server, _, _ = make_server(ingestion=ingestion)
    result = payload(run(server.call_tool('get_paper_status', {'job_id': 'job'})))
    assert result['capabilities'] == []


@pytest.mark.parametrize('top_k', [0, 21, True])
def test_sdk_rejects_out_of_range_top_k(top_k):
    server, answer, _ = make_server()
    with pytest.raises(Exception):
        run(server.call_tool('ask_paper', {
            'context_id': 'context', 'question': '질문', 'top_k': top_k,
        }))
    answer.answer.assert_not_called()
