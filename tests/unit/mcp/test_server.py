"""MCP 도구 스키마·응답·안전한 오류 변환을 검증한다."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.evidence_qa.answer import AnswerGenerationError, AnswerResponse
from solo_leveling.domain.semantic_verification import VerificationLevel
from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Citation, Claim, EvidenceDetail, GetEvidenceResult,
    ReasonCode, RetrievalMethod, RetrievedChunk, SearchResult, SearchScope,
)
from solo_leveling.domain.models import (
    Chunk, JobStatus, LearningContext, RequestConflictError,
)
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
    return AnswerResponse(context_id='context', search=search, result=result)


def make_server(answer=None, evidence=None, ingestion=None, contexts=None):
    answer = answer or Mock()
    evidence = evidence or Mock()
    return create_server(MCPServices(answer, evidence, ingestion, contexts)), answer, evidence


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


def test_ask_tool_description_explains_verification_and_partial():
    server, _, _ = make_server()
    tool = next(tool for tool in run(server.list_tools()) if tool.name == 'ask_paper')
    description = tool.description
    assert 'structural_only는 구조 검사 수준' in description
    assert 'model_checked는 실제 인용 원문' in description
    assert '사실성 보증은 아닙니다' in description
    assert 'model_checked와 partial/extraction_limited' in description
    assert 'partial/semantic_limited' in description
    assert '설정 비활성화를 단정하지 마세요' in description
    assert '배경지식으로 보충하지 마세요' in description


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


def test_ask_preserves_model_checked_level():
    server, answer, _ = make_server()
    answer.answer.return_value = replace(
        answer_response(), verification_level=VerificationLevel.MODEL_CHECKED,
    )
    result = payload(run(server.call_tool('ask_paper',
        {'context_id': 'context', 'question': '질문'})))
    assert result['verification_level'] == 'model_checked'


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
    contexts = Mock()
    server, _, _ = make_server(ingestion=ingestion, contexts=contexts)
    tools = {tool.name: tool for tool in run(server.list_tools())}
    assert set(tools) == {
        'add_paper', 'get_paper_status', 'start_learning', 'ask_paper', 'get_evidence',
        'list_paper_jobs',
    }
    assert tools['add_paper'].annotations.idempotentHint is True
    assert tools['start_learning'].annotations.idempotentHint is True
    assert tools['start_learning'].annotations.openWorldHint is False
    added = payload(run(server.call_tool('add_paper', {
        'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'},
        'request_key': 'request-1',
    })))
    assert added == {'paper_id': 'paper', 'version_id': 'version',
                     'job_id': 'job', 'status': 'processing'}
    status = payload(run(server.call_tool('get_paper_status', {'job_id': 'job'})))
    assert status['capabilities'] == ['start_learning']
    assert status['limitations'] == ['translation_failed:chunk:provider_unavailable']


def test_start_learning_returns_context_for_selected_goal_and_concepts():
    contexts = Mock()
    contexts.get_or_create_learning_context.return_value = LearningContext(
        'context', 'version', 'parse', 'translation', goal='implement',
        known_concepts=['Transformer'], embedding_set_id='embedding',
    )
    server, _, _ = make_server(contexts=contexts)

    result = payload(run(server.call_tool('start_learning', {
        'version_id': 'version',
        'goal': 'implement',
        'known_concepts': ['Transformer'],
    })))

    assert result == {
        'context_id': 'context',
        'version_id': 'version',
        'goal': 'implement',
        'known_concepts': ['Transformer'],
        'status': 'ready',
        'capabilities': ['ask_paper', 'get_evidence'],
    }
    contexts.get_or_create_learning_context.assert_called_once_with(
        'version', 'implement', ['Transformer'],
    )


def test_start_learning_rejects_blank_version_without_storage_call():
    contexts = Mock()
    server, _, _ = make_server(contexts=contexts)

    with pytest.raises(ToolError) as caught:
        run(server.call_tool('start_learning', {'version_id': ' '}))

    assert 'INVALID_ARGUMENT' in str(caught.value)
    contexts.get_or_create_learning_context.assert_not_called()


def test_start_learning_rejects_invalid_known_concepts_without_storage_call():
    contexts = Mock()
    server, _, _ = make_server(contexts=contexts)

    with pytest.raises(ToolError) as caught:
        run(server.call_tool('start_learning', {
            'version_id': 'version', 'known_concepts': ['Transformer', 'Transformer'],
        }))

    assert 'INVALID_ARGUMENT' in str(caught.value)
    contexts.get_or_create_learning_context.assert_not_called()


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


@pytest.mark.parametrize(('name', 'required', 'optional', 'outputs', 'hints'), [
    ('list_paper_jobs', set(), {'query', 'limit', 'cursor'},
     {'jobs', 'next_cursor'}, (True, True, False)),
    ('add_paper', {'source', 'request_key'}, set(),
     {'paper_id', 'version_id', 'job_id', 'status'}, (False, True, True)),
    ('get_paper_status', {'job_id'}, set(),
     {'job_id', 'version_id', 'status', 'stage', 'capabilities', 'limitations',
      'result_available'}, (True, True, False)),
    ('start_learning', {'version_id'}, {'goal', 'known_concepts'},
     {'context_id', 'version_id', 'goal', 'known_concepts', 'status', 'capabilities'},
     (False, True, False)),
    ('ask_paper', {'context_id', 'question'}, {'standalone_question', 'focus', 'top_k'},
     {'context_id', 'verification_level', 'status', 'answer_ko', 'claims',
      'citations', 'reason_code'}, (False, False, True)),
    ('get_evidence', {'context_id', 'evidence_ids'}, set(), {'evidence'},
     (True, True, False)),
])
def test_complete_tool_contract(name, required, optional, outputs, hints):
    server, _, _ = make_server(ingestion=Mock(), contexts=Mock())
    tool = next(tool for tool in run(server.list_tools()) if tool.name == name)
    assert set(tool.inputSchema.get('required', [])) == required
    assert set(tool.inputSchema['properties']) == required | optional
    assert set(tool.outputSchema['required']) == outputs
    assert set(tool.outputSchema['properties']) == outputs
    assert (tool.annotations.readOnlyHint, tool.annotations.idempotentHint,
            tool.annotations.openWorldHint) == hints
    assert tool.annotations.destructiveHint is False


def test_nested_schema_defaults_and_nullable_fields():
    server, _, _ = make_server(ingestion=Mock(), contexts=Mock())
    tools = {tool.name: tool for tool in run(server.list_tools())}
    ask = tools['ask_paper']
    top_k = ask.inputSchema['properties']['top_k']
    assert (top_k['default'], top_k['minimum'], top_k['maximum']) == (5, 1, 20)
    goal = tools['start_learning'].inputSchema['properties']['goal']
    assert goal['default'] == 'understand'
    assert goal['enum'] == ['understand', 'implement', 'skim']
    assert ask.inputSchema['properties']['focus']['default'] is None
    assert ask.inputSchema['properties']['standalone_question']['default'] is None
    source = tools['add_paper'].inputSchema['$defs']['PaperSource']
    assert source['additionalProperties'] is False
    assert set(source['required']) == {'kind', 'relative_path'}
    assert source['properties']['kind']['const'] == 'local_file'
    definitions = ask.outputSchema['$defs']
    assert set(definitions['ClaimOutput']['required']) == {'claim_id', 'text', 'evidence_ids'}
    citation = definitions['CitationOutput']
    assert set(citation['required']) == {
        'evidence_id', 'chunk_id', 'version_id', 'parse_revision_id',
        'translation_revision_id', 'printed_page_label', 'pdf_page',
        'quote_ko', 'quote_original',
    }
    for field in ('translation_revision_id', 'printed_page_label', 'quote_ko'):
        assert {'type': 'null'} in citation['properties'][field]['anyOf']
    assert {'type': 'null'} in ask.outputSchema['properties']['reason_code']['anyOf']
    detail = tools['get_evidence'].outputSchema['$defs']['EvidenceOutput']
    assert set(detail['required']) == set(citation['required']) | {'file_display_name'}


def test_legacy_minimal_ask_keeps_defaults_and_matching_text_content():
    server, answer, _ = make_server()
    answer.answer.return_value = answer_response()
    content, structured = run(server.call_tool('ask_paper', {
        'context_id': 'context', 'question': '질문',
    }))
    answer.answer.assert_called_once_with('질문', context_id='context', top_k=5,
                                          pdf_pages=(), section_ids=())
    assert json.loads(content[0].text) == structured
    assert structured['reason_code'] is None
    assert structured['citations'][0]['printed_page_label'] is None


@pytest.mark.parametrize('concepts', [None, []])
def test_legacy_start_learning_defaults(concepts):
    contexts = Mock()
    contexts.get_or_create_learning_context.return_value = LearningContext(
        'context', 'version', 'parse', 'translation', embedding_set_id='embedding',
    )
    server, _, _ = make_server(contexts=contexts)
    arguments = {'version_id': 'version'}
    if concepts is not None:
        arguments['known_concepts'] = concepts
    result = payload(run(server.call_tool('start_learning', arguments)))
    contexts.get_or_create_learning_context.assert_called_once_with('version', 'understand', [])
    assert result['goal'] == 'understand'
    assert result['known_concepts'] == []


@pytest.mark.parametrize(('name', 'arguments'), [
    ('add_paper', {'source': {'kind': 'url', 'relative_path': 'paper.pdf'}, 'request_key': 'r'}),
    ('add_paper', {'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'}}),
    ('get_paper_status', {}),
    ('start_learning', {'version_id': 'v', 'goal': 'unknown'}),
    ('ask_paper', {'context_id': 'c'}),
    ('get_evidence', {'context_id': 'c', 'evidence_ids': None}),
])
def test_invalid_schema_never_calls_services(name, arguments):
    ingestion, contexts = Mock(), Mock()
    server, answer, evidence = make_server(ingestion=ingestion, contexts=contexts)
    with pytest.raises(Exception):
        run(server.call_tool(name, arguments))
    for service in (ingestion, contexts, answer, evidence):
        assert service.mock_calls == []


@pytest.mark.parametrize(('status', 'reason'), [
    (AnswerStatus.PARTIAL, ReasonCode.EXTRACTION_LIMITED),
    (AnswerStatus.INSUFFICIENT_EVIDENCE, ReasonCode.EVIDENCE_NOT_FOUND),
    (AnswerStatus.NEEDS_CLARIFICATION, None),
])
def test_answer_status_is_not_a_tool_error(status, reason):
    server, answer, _ = make_server()
    response = answer_response()
    partial = status == AnswerStatus.PARTIAL
    response = replace(response, result=AnswerResult(
        status, '주장' if partial else '근거를 확인할 수 없습니다.',
        response.result.claims if partial else (),
        response.result.citations if partial else (), reason,
    ))
    answer.answer.return_value = response
    result = payload(run(server.call_tool('ask_paper', {
        'context_id': 'context', 'question': '질문',
    })))
    assert result['status'] == status.value
    assert result['reason_code'] == (reason.value if reason is not None else None)
    assert bool(result['claims']) is partial


@pytest.mark.parametrize(('error', 'message'), [
    (InvalidArgumentError('secret'), 'INVALID_ARGUMENT: 입력값을 확인해주세요.'),
    (ResourceNotFoundError('secret'), 'NOT_FOUND: 요청한 자료를 찾을 수 없습니다.'),
    (RequestConflictError('secret'), 'CONFLICT: request_key가 이전 요청과 일치하지 않습니다.'),
    (UnsupportedDocumentError('secret'), 'UNSUPPORTED_DOCUMENT: 지원하는 PDF 파일인지 확인해주세요.'),
    (AnswerGenerationError('secret'), 'UPSTREAM_UNAVAILABLE: 답변 생성 서비스를 사용할 수 없습니다.'),
    (DataIntegrityError('secret'), 'INTERNAL_ERROR: 저장된 데이터의 연결을 확인하지 못했습니다.'),
    (RuntimeError('secret'), 'INTERNAL_ERROR: 요청을 처리하지 못했습니다.'),
    (ContextNotReadyError('v', 'job', JobStatus.PROCESSING),
     'PAPER_NOT_READY: 등록 작업 상태를 확인해주세요. job_id=job, status=processing'),
])
def test_safe_service_message_contract(error, message):
    from solo_leveling.interfaces.mcp.errors import to_tool_error

    assert str(to_tool_error(error)) == message


def test_list_jobs_defaults_and_empty_result():
    ingestion = Mock()
    ingestion.list_jobs.return_value = {'jobs': [], 'next_cursor': None}
    server, _, _ = make_server(ingestion=ingestion)
    assert payload(run(server.call_tool('list_paper_jobs', {}))) == {
        'jobs': [], 'next_cursor': None}
    ingestion.list_jobs.assert_called_once_with(query=None, limit=20, cursor=None)
    ingestion.add_local_pdf.assert_not_called()


@pytest.mark.parametrize('arguments', [
    {'limit': True}, {'limit': '2'}, {'limit': 0}, {'limit': 101},
    {'query': 1}, {'query': ''}, {'query': 'x' * 201},
    {'cursor': 2}, {'cursor': ''}, {'cursor': 'x' * 2049},
])
def test_list_invalid_schema_never_calls_service(arguments):
    ingestion = Mock()
    server, _, _ = make_server(ingestion=ingestion)
    with pytest.raises(Exception):
        run(server.call_tool('list_paper_jobs', arguments))
    ingestion.list_jobs.assert_not_called()


def test_list_cursor_error_is_safe():
    ingestion = Mock()
    ingestion.list_jobs.side_effect = InvalidArgumentError('private-data')
    server, _, _ = make_server(ingestion=ingestion)
    with pytest.raises(ToolError) as error:
        run(server.call_tool('list_paper_jobs', {'cursor': 'bad'}))
    assert 'INVALID_ARGUMENT' in str(error.value)
    assert 'private-data' not in str(error.value)
