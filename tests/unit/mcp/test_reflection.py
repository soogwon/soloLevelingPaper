"""실제 SQLite와 MCP 경계에서 읽기 전 답변의 저장·조회 계약을 검증한다."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.application.reflection.service import ReflectionService
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.interfaces.mcp.lifecycle import RequestGate
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_learning_persistence import publish, setup_db  # noqa: F401


def server_for(db, service=None):
    return create_server(MCPServices(Mock(), Mock(),
                                    reflection=service or ReflectionService(db)))


def call(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))[1]


@pytest.fixture
def ctx(setup_db):  # noqa: F811
    db, chunks = setup_db
    context = publish(db, chunks)
    return db, context.context_id, server_for(db)


def test_schema_and_hints(ctx):
    _, _, server = ctx
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    read = tools['get_reading_questions']
    save = tools['save_reading_answer']
    assert read.annotations.readOnlyHint and read.annotations.idempotentHint
    assert not save.annotations.readOnlyHint and save.annotations.destructiveHint
    assert not save.annotations.idempotentHint and not save.annotations.openWorldHint
    assert save.inputSchema['properties']['phase']['const'] == 'before'
    assert save.inputSchema['properties']['text']['maxLength'] == 5000
    assert read.inputSchema['additionalProperties'] is False
    assert save.inputSchema['additionalProperties'] is False
    assert 'items' in read.outputSchema['required']


def test_create_edit_and_reopen_server_preserves_text_and_context(ctx, capsys):
    db, context_id, server = ctx
    initial = call(server, 'get_reading_questions', context_id=context_id)
    assert (initial['total'], initial['written'], initial['status']) == (3, 0, 'not_started')
    assert all(i['answer'] is None for i in initial['items'])
    text = '  사용자 작성 답변\n한글 😀 e\u0301  '
    first = call(server, 'save_reading_answer', context_id=context_id,
                 template_id='before.background', text=text)
    assert first['items'][0]['answer']['text'] == text
    assert first['status'] == 'in_progress'
    second = call(server, 'save_reading_answer', context_id=context_id,
                  template_id='before.background', text='수정한 답', status='completed')
    assert second['items'][0]['answer']['created_at'] == first['items'][0]['answer']['created_at']
    reopened = call(server_for(db), 'get_reading_questions', context_id=context_id)
    assert reopened == second
    assert len(repo.get_reflection_answers(db, context_id, 'before')) == 1
    output = capsys.readouterr()
    assert text not in output.out + output.err
    assert '수정한 답' not in output.out + output.err


def test_filter_counts_and_context_isolation(ctx):
    db, context_id, server = ctx
    progress_before = repo.get_learning_progress(db, context_id)
    other = replace(repo.get_learning_context(db, context_id), context_id='other')
    repo.create_learning_context(db, other)
    for key in ('background', 'hypothesis', 'expectation'):
        result = call(server, 'save_reading_answer', context_id=context_id,
                      template_id=f'before.{key}', text='사용자 답', status='completed')
    assert (result['completed'], result['status']) == (3, 'completed')
    assert repo.get_learning_progress(db, context_id) == progress_before
    assert call(server, 'get_reading_questions', context_id='other')['written'] == 0
    filtered = call(server, 'get_reading_questions', context_id=context_id, step='review')
    assert filtered['items'] == [] and filtered['total'] == 0
    assert filtered['status'] == 'not_started'
    assert call(server, 'get_reading_questions', context_id=context_id, step='preview')['completed'] == 3


def test_blank_draft_is_saved_but_not_completed(ctx):
    _, context_id, server = ctx
    result = call(server, 'save_reading_answer', context_id=context_id,
                  template_id='before.background', text='')
    assert result['written'] == 1 and result['completed'] == 0
    assert result['items'][0]['answer']['text'] == ''


@pytest.mark.parametrize('changes', [
    {'context_id': ' '}, {'phase': 'after'}, {'phase': None},
    {'template_id': 'private-invalid-template'}, {'status': 'submitted'},
    {'text': '  ', 'status': 'completed'}, {'text': 'x' * 5001},
    {'text': 123}, {'context_id': True},
])
def test_invalid_save_writes_nothing(ctx, changes):
    db, context_id, server = ctx
    args = dict(context_id=context_id, template_id='before.background', text='답')
    args.update(changes)
    with pytest.raises(ToolError):
        call(server, 'save_reading_answer', **args)
    assert repo.get_reflection_answers(db, context_id, 'before') == []


@pytest.mark.parametrize('name,args', [
    ('get_reading_questions', {}),
    ('save_reading_answer', {'template_id': 'before.background', 'text': '답'}),
])
def test_missing_context_returns_safe_not_found(ctx, name, args):
    _, _, server = ctx
    with pytest.raises(ToolError, match='NOT_FOUND'):
        call(server, name, context_id='missing', **args)


def test_domain_error_and_storage_error_do_not_expose_input(ctx):
    db, context_id, server = ctx
    with pytest.raises(ToolError, match='INVALID_ARGUMENT') as error:
        call(server, 'save_reading_answer', context_id=context_id,
             template_id='private-invalid-template', text='private-answer')
    assert 'private' not in str(error.value)
    service = Mock()
    service.save_answer.side_effect = RuntimeError('private-db-path-and-answer')
    with pytest.raises(ToolError, match='INTERNAL_ERROR') as error:
        call(server_for(db, service), 'save_reading_answer', context_id=context_id,
             template_id='before.background', text='private-answer')
    assert 'private' not in str(error.value)


def test_shutdown_blocks_reflection_writes(ctx):
    db, context_id, _ = ctx
    gate = RequestGate()
    server = server_for(db, gate.wrap(ReflectionService(db)))
    gate.stop()
    with pytest.raises(ToolError, match='SERVER_SHUTTING_DOWN'):
        call(server, 'save_reading_answer', context_id=context_id,
             template_id='before.background', text='답')
    assert repo.get_reflection_answers(db, context_id, 'before') == []


def test_protocol_roundtrip_and_reconnect(ctx):
    db, context_id, server = ctx

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('save_reading_answer', {
                'context_id': context_id, 'template_id': 'before.hypothesis',
                'text': '사용자가 작성한 가설', 'status': 'draft',
            })
            assert not result.isError
            assert json.loads(result.content[0].text) == result.structuredContent
            invalid = await client.call_tool('save_reading_answer', {
                'context_id': context_id, 'template_id': 'private-invalid', 'text': '비공개 답',
            })
            assert invalid.isError and 'INVALID_ARGUMENT' in invalid.content[0].text
            assert 'private-invalid' not in invalid.content[0].text
        async with create_connected_server_and_client_session(server_for(db)) as client:
            result = await client.call_tool('get_reading_questions', {'context_id': context_id})
            assert not result.isError
            assert result.structuredContent['items'][1]['answer']['text'] == '사용자가 작성한 가설'

    asyncio.run(scenario())


@pytest.mark.parametrize('name,changes', [
    ('save_reading_answer', {'text': {'secret': 'PRIVATE_INPUT_MARKER'}}),
    ('save_reading_answer', {'text': 'PRIVATE_INPUT_MARKER' * 300}),
    ('save_reading_answer', {'status': 'PRIVATE_INPUT_MARKER'}),
    ('save_reading_answer', {'context_id': ['PRIVATE_INPUT_MARKER']}),
    ('save_reading_answer', {'expected_revision': 'PRIVATE_INPUT_MARKER'}),
    ('save_reading_answer', {'statuz': 'PRIVATE_INPUT_MARKER'}),
    ('get_reading_questions', {'step': 'PRIVATE_INPUT_MARKER'}),
    ('get_reading_questions', {'phase': 'PRIVATE_INPUT_MARKER'}),
    ('get_reading_questions', {'unknown': 'PRIVATE_INPUT_MARKER'}),
    ('get_reading_questions', {'context_id': {'secret': 'PRIVATE_INPUT_MARKER'}}),
])
def test_protocol_rejects_unsafe_input_before_service(ctx, name, changes, capsys, caplog):
    db, context_id, _ = ctx
    actual = ReflectionService(db)
    actual.save_answer(context_id, 'before', 'before.background', '보존할 기존 답변', 'completed')
    before = actual.get_session(context_id, 'before')
    spy = Mock(wraps=actual)
    server = server_for(db, spy)
    args = {'context_id': context_id}
    if name == 'save_reading_answer':
        args.update(template_id='before.background', text='수정할 답변')
    args.update(changes)

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool(name, args)
            assert result.isError
            assert result.content[0].text == 'INVALID_ARGUMENT: 입력값을 확인해주세요.'
            assert result.structuredContent is None
            spy.get_session.assert_not_called()
            spy.save_answer.assert_not_called()
            # 검증 실패 뒤에도 같은 연결에서 정상 조회할 수 있다.
            valid = await client.call_tool('get_reading_questions', {'context_id': context_id})
            assert not valid.isError

    asyncio.run(scenario())
    assert actual.get_session(context_id, 'before') == before
    captured = capsys.readouterr()
    assert 'PRIVATE_INPUT_MARKER' not in captured.out + captured.err + caplog.text


def test_missing_required_field_is_safe_without_calling_service(ctx):
    db, context_id, _ = ctx
    spy = Mock()
    server = server_for(db, spy)

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('save_reading_answer', {
                'context_id': context_id, 'template_id': 'before.background',
            })
            assert result.isError
            assert result.content[0].text == 'INVALID_ARGUMENT: 입력값을 확인해주세요.'
            spy.save_answer.assert_not_called()

    asyncio.run(scenario())
