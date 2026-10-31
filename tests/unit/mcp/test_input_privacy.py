"""메모리 전송 MCP 세션에서 입력 오류의 본문 비노출을 검증한다."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.interfaces.mcp.server import MCPServices, create_server


MARKER = 'SYNTHETIC_PRIVATE_ISSUE64'
SAFE_ERROR = 'INVALID_ARGUMENT: 입력값을 확인해주세요.'
INTERNAL_ERROR = 'INTERNAL_ERROR: 요청을 처리하지 못했습니다.'


@pytest.mark.parametrize('name, arguments', [
    ('ask_paper', {'context_id': 'ctx', 'question': {'private': MARKER}}),
    ('ask_paper', {'context_id': MARKER}),
    ('ask_paper', {'context_id': 'ctx', 'question': MARKER,
                   'focus': {'pdf_pages': [MARKER]}}),
    ('ask_paper', {'context_id': 'ctx', 'question': '질문',
                   'focus': {'unexpected': MARKER}}),
    ('get_evidence', {'context_id': 'ctx', 'evidence_ids': [ {'private': MARKER} ]}),
    (MARKER, {}),
])
def test_protocol_input_error_is_safe_and_session_survives(name, arguments, caplog, capsys):
    answer, evidence, ingestion = Mock(), Mock(), Mock()
    ingestion.list_jobs.return_value = {'jobs': [], 'next_cursor': None}
    server = create_server(MCPServices(answer, evidence, ingestion))

    async def scenario():
        async with create_connected_server_and_client_session(
            server, read_timeout_seconds=timedelta(seconds=10),
        ) as client:
            result = await client.call_tool(name, arguments)
            assert result.isError
            assert [item.text for item in result.content] == [SAFE_ERROR]
            assert MARKER not in result.model_dump_json()
            answer.answer.assert_not_called()
            evidence.get.assert_not_called()
            ingestion.list_jobs.assert_not_called()
            # 같은 세션의 다음 정상 요청까지 처리되는지 확인한다.
            result = await client.call_tool('list_paper_jobs', {})
            assert not result.isError
            assert result.structuredContent == {'jobs': [], 'next_cursor': None}

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize('invalid_output', [
    {'jobs': MARKER, 'next_cursor': None},
    {'jobs': [], 'next_cursor': {'private': MARKER}},
])
def test_output_model_error_is_safe_and_session_survives(invalid_output, caplog, capsys):
    ingestion = Mock()
    ingestion.list_jobs.return_value = invalid_output
    server = create_server(MCPServices(Mock(), Mock(), ingestion))

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('list_paper_jobs', {})
            assert result.isError
            assert [item.text for item in result.content] == [INTERNAL_ERROR]
            assert result.structuredContent is None
            assert MARKER not in result.model_dump_json()
            ingestion.list_jobs.return_value = {'jobs': [], 'next_cursor': None}
            result = await client.call_tool('list_paper_jobs', {})
            assert not result.isError
            assert result.structuredContent == {'jobs': [], 'next_cursor': None}

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err + caplog.text


def test_output_json_schema_error_is_safe(caplog, capsys):
    """출력 모델 이후의 프로토콜 스키마 검사도 본문을 노출하지 않는다."""
    ingestion = Mock()
    ingestion.list_jobs.return_value = {'jobs': [], 'next_cursor': MARKER}
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    # SDK 모델은 문자열을 허용하지만 공개 스키마가 더 엄격한 상황을 재현한다.
    tool = server._tool_manager.get_tool('list_paper_jobs')
    tool.output_schema['properties']['next_cursor'] = {'type': 'null'}

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('list_paper_jobs', {})
            assert result.isError
            assert [item.text for item in result.content] == [INTERNAL_ERROR]
            assert MARKER not in result.model_dump_json()

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err + caplog.text


def test_output_serialization_error_is_safe_and_session_survives(caplog, capsys):
    """출력 모델 검사 전에 발생하는 JSON 직렬화 오류도 본문 없이 반환한다."""
    ingestion = Mock()
    ingestion.list_jobs.return_value = {
        'jobs': [], 'next_cursor': MARKER.encode() + b'\xff',
    }
    server = create_server(MCPServices(Mock(), Mock(), ingestion))

    async def scenario():
        async with create_connected_server_and_client_session(
            server, read_timeout_seconds=timedelta(seconds=10),
        ) as client:
            result = await client.call_tool('list_paper_jobs', {})
            assert result.isError
            assert [item.text for item in result.content] == [INTERNAL_ERROR]
            assert result.structuredContent is None
            assert MARKER not in result.model_dump_json()
            ingestion.list_jobs.return_value = {'jobs': [], 'next_cursor': None}
            result = await client.call_tool('list_paper_jobs', {})
            assert not result.isError
            assert result.structuredContent == {'jobs': [], 'next_cursor': None}
            assert ingestion.list_jobs.call_count == 2

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err + caplog.text
    assert 'Error serializing to JSON' not in captured.out + captured.err + caplog.text


def test_service_error_code_is_preserved(caplog, capsys):
    from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError

    ingestion = Mock()
    ingestion.list_jobs.side_effect = ResourceNotFoundError(MARKER)
    server = create_server(MCPServices(Mock(), Mock(), ingestion))

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('list_paper_jobs', {})
            assert result.isError
            assert 'NOT_FOUND: 요청한 자료를 찾을 수 없습니다.' in result.content[0].text
            assert 'INTERNAL_ERROR' not in result.content[0].text
            assert MARKER not in result.model_dump_json()

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err + caplog.text


def test_protocol_preserves_sdk_json_string_conversion():
    """Desktop가 보내던 JSON 문자열 형태 배열도 기존과 같이 처리한다."""
    from solo_leveling.interfaces.mcp.input_privacy import SafeInputServer

    server = SafeInputServer('synthetic')

    @server.tool()
    def echo(values: list[str]) -> dict:
        return {'values': values}

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('echo', {'values': '["synthetic"]'})
            assert not result.isError
            assert json.loads(result.content[0].text) == {'values': ['synthetic']}

    asyncio.run(scenario())

