"""미리보기의 입력·권한 실패와 조회 전용 MCP 계약을 검증한다."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest
from mcp.server.fastmcp import FastMCP
from solo_leveling.interfaces.http.remote_recovery_preview import RemoteRecoveryPreview
from solo_leveling.interfaces.mcp.remote_product import register_product_queries


@pytest.mark.parametrize('context,operation', [('bad', 'reparse'), (None, 'reparse'),
    (str(uuid4()), 'retry'), (str(uuid4()), None)])
def test_invalid_input_does_not_resolve_owner(context, operation):
    queries = Mock()
    assert RemoteRecoveryPreview(queries).preview(context, operation) == {'status': 'invalid_argument'}
    queries.owner.assert_not_called()
    queries.pool.connection.assert_not_called()


def test_denied_owner_does_not_access_context_or_pool():
    queries = Mock()
    queries.owner.return_value = None
    assert RemoteRecoveryPreview(queries).preview(str(uuid4()), 'reparse') == {'status': 'not_found'}
    queries.contexts.resolve.assert_not_called()
    queries.pool.connection.assert_not_called()


def test_failure_does_not_expose_database_error():
    queries = Mock()
    queries.owner.side_effect = RuntimeError('비밀 접속 정보')
    assert RemoteRecoveryPreview(queries).preview(str(uuid4()), 'reparse') == {'status': 'unavailable'}


def test_tool_is_structured_read_only_and_delegates():
    server = FastMCP('합성')
    queries = NS(owner=lambda: None)
    register_product_queries(server, queries)
    async def run():
        tools = {tool.name: tool for tool in await server.list_tools()}
        tool = tools['preview_remote_reprocessing']
        assert tool.annotations.readOnlyHint and tool.annotations.idempotentHint
        assert not tool.annotations.destructiveHint and not tool.annotations.openWorldHint
        assert tool.outputSchema is not None
        result = await server.call_tool('preview_remote_reprocessing',
            {'context_id': str(uuid4()), 'operation': 'reparse'})
        assert 'not_found' in str(result)
    asyncio.run(run())
