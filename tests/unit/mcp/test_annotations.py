"""실제 SQLite와 MCP 전송으로 기록 보존·충돌·위치 확인·오류 비노출을 검증한다."""
import asyncio
from contextlib import closing
import json
from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_learning_persistence import setup_db, publish


@pytest.fixture
def environment(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks).context_id
    return db, context


def server(db):
    return create_server(MCPServices(Mock(), Mock(), annotations=AnnotationService(db)))


async def call(client, name, request):
    result = await client.call_tool(name, {'request': request})
    assert not result.isError, result
    assert json.loads(result.content[0].text) == result.structuredContent
    return result.structuredContent


def test_persistence_pagination_conflict_and_location(environment):
    db, context = environment
    scope = {'context_id': context}
    segment = {'chunk_id': 'c1', 'start_offset': 0, 'end_offset': 5, 'quote_snapshot': 'First'}

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            first = await call(client, 'create_annotation', {
                **scope, 'kind': 'note', 'body': '저장할 메모', 'segments': [segment], 'step': 'review'})
            await call(client, 'create_annotation', {**scope, 'kind': 'highlight', 'body': '', 'segments': [segment]})
            page = await call(client, 'list_annotations', {**scope, 'limit': 1})
            following = await call(client, 'list_annotations', {**scope, 'limit': 1, 'cursor': page['next_cursor']})
            assert len(page['items']) == len(following['items']) == 1
            assert page['items'][0]['annotation_id'] != following['items'][0]['annotation_id']
            assert following['next_cursor'] is None
            record = first['annotation']
        async with create_connected_server_and_client_session(server(db)) as client:
            target = {**scope, 'annotation_id': record['annotation_id']}
            detail = await call(client, 'get_annotation', target)
            assert detail['annotation']['body'] == '저장할 메모'
            assert detail['segment_available'] == [True]
            assert detail['location_status'] == 'available'
            changed = await call(client, 'update_annotation', {**target, 'expected_revision': 1, 'body': '수정'})
            assert changed['annotation']['step'] == 'review'
            conflict = await client.call_tool('update_annotation', {'request': {**target, 'expected_revision': 1, 'body': '덮어쓰기'}})
            assert conflict.isError and 'CONFLICT' in conflict.content[0].text
            cleared = await call(client, 'update_annotation', {**target, 'expected_revision': 2, 'step': None})
            assert cleared['annotation']['body'] == '수정' and cleared['annotation']['step'] is None
            wrong = await client.call_tool('get_annotation', {'request': {**target, 'context_id': 'other'}})
            missing = await client.call_tool('get_annotation', {'request': {**scope, 'annotation_id': 'missing'}})
            assert wrong.isError and wrong.content == missing.content
            with closing(get_connection(db)) as conn, conn:
                conn.execute("UPDATE chunks SET original_text='Other.' WHERE chunk_id='c1'")
            detail = await call(client, 'get_annotation', target)
            assert detail['segment_available'] == [False] and detail['location_status'] == 'unavailable'
            deleted = await call(client, 'delete_annotation', {**target, 'expected_revision': 3})
            assert deleted['outcome'] == 'deleted'
    asyncio.run(scenario())


@pytest.mark.parametrize('name,fields,reason', [
    ('list_annotations', {'limit': 51}, 'INVALID_LIMIT'),
    ('list_annotations', {'cursor': 'PRIVATE_MARKER'}, 'INVALID_CURSOR'),
    ('create_annotation', {'kind': 'note', 'body': 'PRIVATE_MARKER', 'segments': [
        {'chunk_id': 'c1', 'start_offset': 0, 'end_offset': 5, 'quote_snapshot': 'First', 'target': 'translation'}]}, 'TRANSLATION_NOT_SUPPORTED'),
    ('create_annotation', {'kind': 'note', 'body': {'secret': 'PRIVATE_MARKER'}}, 'INVALID_ARGUMENT'),
    ('create_annotation', {'kind': 'note', 'body': 'PRIVATE_MARKER', 'segments': [
        {'chunk_id': 'c1', 'start_offset': True, 'end_offset': 5, 'quote_snapshot': 'First'}]}, 'INVALID_ARGUMENT'),
])
def test_invalid_inputs_hide_user_text(environment, name, fields, reason, caplog, capsys):
    db, context = environment
    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            result = await client.call_tool(name, {'request': {'context_id': context, **fields}})
            assert result.isError and reason in result.content[0].text
            assert 'PRIVATE_MARKER' not in str(result)
            page = await call(client, 'list_annotations', {'context_id': context})
            assert page['items'] == []
    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert 'PRIVATE_MARKER' not in caplog.text + captured.out + captured.err


def test_stdio_restart_preserves_annotation(environment, tmp_path):
    import os
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    db, context = environment
    root = Path(__file__).resolve().parents[3]
    program = '''
import sys
from unittest.mock import Mock
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
create_server(MCPServices(Mock(), Mock(), annotations=AnnotationService(sys.argv[1]))).run(transport='stdio')
'''
    params = StdioServerParameters(command=sys.executable, args=['-c', program, db], cwd=root,
        env={**os.environ, 'PYTHONPATH': str(root / 'src')})

    async def scenario():
        with (tmp_path / 'stderr.log').open('w') as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    saved = await call(client, 'create_annotation', {
                        'context_id': context, 'kind': 'note', 'body': '재시작 후 유지'})
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    restored = await call(client, 'get_annotation', {
                        'context_id': context, 'annotation_id': saved['annotation']['annotation_id']})
                    assert restored['annotation'] == saved['annotation']
                    assert restored['location_status'] == 'not_applicable'
    asyncio.run(scenario())


@pytest.mark.parametrize('operation', ['update_annotation', 'delete_annotation'])
def test_other_context_mutation_hides_record_and_preserves_it(environment, operation):
    db, context = environment
    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            saved = await call(client, 'create_annotation', {'context_id': context, 'kind': 'note', 'body': '보존'})
            record = saved['annotation']
            request = {'context_id': 'other', 'annotation_id': record['annotation_id'], 'expected_revision': 1}
            if operation == 'update_annotation':
                request['body'] = '변경 시도'
            wrong = await client.call_tool(operation, {'request': request})
            missing = await client.call_tool(operation, {'request': {**request, 'annotation_id': 'missing'}})
            assert wrong.isError and wrong.content == missing.content
            restored = await call(client, 'get_annotation', {'context_id': context, 'annotation_id': record['annotation_id']})
            assert restored['annotation'] == record
    asyncio.run(scenario())


def test_rejected_update_and_filter_cursor_preserve_records(environment):
    db, context = environment
    segment = {'chunk_id': 'c1', 'start_offset': 0, 'end_offset': 5, 'quote_snapshot': 'First'}
    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            scope = {'context_id': context}
            saved = await call(client, 'create_annotation', {**scope, 'kind': 'note', 'body': '보존', 'segments': [segment]})
            await call(client, 'create_annotation', {**scope, 'kind': 'note', 'body': '둘째'})
            target = {**scope, 'annotation_id': saved['annotation']['annotation_id']}
            mixed = await client.call_tool('update_annotation', {'request': {
                **target, 'expected_revision': 1, 'body': '저장되면 안 되는 수정',
                'segments': [segment, {**segment, 'target': 'translation'}]}})
            assert mixed.isError and 'TRANSLATION_NOT_SUPPORTED' in mixed.content[0].text
            restored = await call(client, 'get_annotation', target)
            assert restored['annotation'] == saved['annotation']
            page = await call(client, 'list_annotations', {**scope, 'limit': 1})
            cursor = await client.call_tool('list_annotations', {'request': {
                **scope, 'limit': 1, 'cursor': page['next_cursor'], 'step': 'review'}})
            assert cursor.isError and 'INVALID_CURSOR' in cursor.content[0].text
            stale = await client.call_tool('delete_annotation', {'request': {**target, 'expected_revision': 2}})
            assert stale.isError and 'CONFLICT' in stale.content[0].text
            restored = await call(client, 'get_annotation', target)
            assert restored['annotation'] == saved['annotation']
    asyncio.run(scenario())


def test_public_schema_and_unknown_field_do_not_expose_input(environment, caplog, capsys):
    db, context = environment
    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools['update_annotation'].annotations.destructiveHint is True
            assert tools['delete_annotation'].annotations.destructiveHint is True
            for name in ('create_annotation', 'get_annotation', 'list_annotations', 'update_annotation', 'delete_annotation'):
                assert tools[name].inputSchema['additionalProperties'] is False
            result = await client.call_tool('create_annotation', {
                'request': {'context_id': context, 'kind': 'note', 'body': 'PRIVATE_MARKER'},
                'PRIVATE_MARKER': 'PRIVATE_MARKER'})
            assert result.isError and 'PRIVATE_MARKER' not in str(result)
            assert (await call(client, 'list_annotations', {'context_id': context}))['items'] == []
    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert 'PRIVATE_MARKER' not in caplog.text + captured.out + captured.err


def test_source_click_revalidates_each_segment_in_its_context(environment):
    db, context = environment

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            saved = await call(client, 'create_annotation', {
                'context_id': context, 'kind': 'highlight', 'body': '',
                'segments': [
                    {'chunk_id': 'c1', 'start_offset': 1, 'end_offset': 4, 'quote_snapshot': 'irs'},
                    {'chunk_id': 'c2', 'start_offset': 0, 'end_offset': 6, 'quote_snapshot': 'Second'},
                ],
            })
            target = {'context_id': context, 'annotation_id': saved['annotation']['annotation_id']}
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            resource = await client.read_resource(tools['get_annotation'].meta['ui']['resourceUri'])
            assert '<title>메모와 원문</title>' in resource.contents[0].text
            assert (await call(client, 'get_annotation', target))['source'] is None
            selected = {**target, 'source_position': 0}
            assert (await call(client, 'get_annotation', selected))['source'] == {
                'position': 0, 'pdf_page': 1, 'before': 'F', 'quote': 'irs', 'after': 't.',
            }
            wrong = await client.call_tool('get_annotation', {'request': {**selected, 'context_id': 'other'}})
            assert wrong.isError and 'NOT_FOUND' in wrong.content[0].text
            with closing(get_connection(db)) as conn, conn:
                conn.execute("UPDATE chunks SET original_text='private replacement' WHERE chunk_id='c1'")
            refreshed = await call(client, 'get_annotation', selected)
            assert refreshed['source'] is None and refreshed['segment_available'] == [False, True]
            assert 'private replacement' not in str(refreshed)
            other = await call(client, 'get_annotation', {**target, 'source_position': 1})
            assert other['source']['quote'] == 'Second' and other['source']['pdf_page'] == 2
    asyncio.run(scenario())
