"""질문 응답을 받지 못한 호출자는 공개 MCP 도구로 기존 실행만 조회한다."""
import asyncio
import json
from unittest.mock import Mock

from mcp.shared.memory import create_connected_server_and_client_session
from solo_leveling.interfaces.mcp.server import create_server, MCPServices
from tests.unit.test_ask_execution_service import env, setup_db


def test_lookup_while_running_and_after_reconnect(env):
    _, ctx, executions, entered, release, calls = env
    server = create_server(MCPServices(Mock(), Mock(), executions=executions))
    scope = {'context_id': ctx, 'request_key': 'key'}

    async def lookup(client, **changes):
        result = await client.call_tool('get_paper_question_status', {**scope, **changes})
        assert not result.isError
        assert json.loads(result.content[0].text) == result.structuredContent
        return result.structuredContent

    async def scenario():
        async with create_connected_server_and_client_session(server) as client:
            pending = asyncio.create_task(client.call_tool(
                'submit_paper_question', {**scope, 'question': '합성 질문'}))
            try:
                assert await asyncio.to_thread(entered.wait, 4)
                assert (await lookup(client))['state'] == 'running'
                assert (await lookup(client, context_id='other'))['state'] == 'not_found'
            finally:
                release.set()
            response = await pending
            assert not response.isError
        async with create_connected_server_and_client_session(server) as client:
            restored = await lookup(client)
            assert restored == response.structuredContent
            assert restored['state'] == 'completed'
            assert len(restored['answer']['citations']) == 2
            conflict = await client.call_tool('submit_paper_question', {**scope, 'question': '다른 질문'})
            assert conflict.isError and 'CONFLICT' in conflict.content[0].text
        assert len(calls) == 1

    asyncio.run(scenario())


def test_cancelled_submit_keeps_result_for_lookup(env):
    import anyio

    _, ctx, executions, entered, release, calls = env
    server = create_server(MCPServices(Mock(), Mock(), executions=executions))
    scope = {'context_id': ctx, 'request_key': 'cancelled-key'}
    replies = []

    async def submit(*, task_status):
        with anyio.CancelScope() as cancel_scope:
            task_status.started(cancel_scope)
            replies.append(await server.call_tool(
                'submit_paper_question', {**scope, 'question': '합성 질문'}))

    async def scenario():
        async with anyio.create_task_group() as group:
            cancel_scope = await group.start(submit)
            try:
                assert await asyncio.to_thread(entered.wait, 4)
                cancel_scope.cancel()
            finally:
                release.set()
        assert not replies
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool('get_paper_question_status', scope)
            assert not result.isError and result.structuredContent['state'] == 'completed'
            assert len(result.structuredContent['answer']['citations']) == 2
        assert len(calls) == 1

    asyncio.run(scenario())
