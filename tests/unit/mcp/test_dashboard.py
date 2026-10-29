"""대시보드 MCP 계약과 안전한 오류·텍스트 폴백을 검증한다."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.interfaces.mcp.dashboard import DASHBOARD_URI
from solo_leveling.infrastructure.database.dashboard_queries import DashboardReader
from tests.unit.test_learning_persistence import setup_db, publish


def test_dashboard_contract_and_same_text_payload(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    reader = DashboardReader(db)
    server = create_server(MCPServices(Mock(), Mock(), dashboard=reader))
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    tool = tools['get_learning_dashboard']
    assert tool.inputSchema['required'] == ['context_id']
    assert set(tool.inputSchema['properties']) == {'context_id'}
    assert tool.annotations.readOnlyHint and tool.annotations.idempotentHint
    assert not tool.annotations.openWorldHint and not tool.annotations.destructiveHint
    assert tool.meta['ui']['resourceUri'] == DASHBOARD_URI
    contents, structured = asyncio.run(server.call_tool('get_learning_dashboard', {'context_id': 'ctx'}))
    assert structured == reader.get('ctx')
    assert json.loads(contents[0].text) == structured
    resources = asyncio.run(server.list_resources())
    resource = next(r for r in resources if str(r.uri) == DASHBOARD_URI)
    assert resource.mimeType == 'text/html;profile=mcp-app'
    assert resource.meta['ui']['csp']['connectDomains'] == []
    html = asyncio.run(server.read_resource(DASHBOARD_URI))
    assert '학습 대시보드' in list(html)[0].content


def test_dashboard_missing_context_and_errors_are_safe(setup_db):
    db, _ = setup_db
    server = create_server(MCPServices(Mock(), Mock(), dashboard=DashboardReader(db)))
    for context_id, code in [('missing', 'NOT_FOUND'), (' ', 'INVALID_ARGUMENT')]:
        with pytest.raises(ToolError, match=code):
            asyncio.run(server.call_tool('get_learning_dashboard', {'context_id': context_id}))
    reader = Mock()
    reader.get.side_effect = RuntimeError('secret/path/key')
    server = create_server(MCPServices(Mock(), Mock(), dashboard=reader))
    with pytest.raises(ToolError) as error:
        asyncio.run(server.call_tool('get_learning_dashboard', {'context_id': 'ctx'}))
    assert 'INTERNAL_ERROR' in str(error.value) and 'secret' not in str(error.value)


@pytest.mark.parametrize('value', ['', 1, True, None])
def test_bad_input_never_calls_reader(value):
    reader = Mock()
    server = create_server(MCPServices(Mock(), Mock(), dashboard=reader))
    with pytest.raises(Exception):
        asyncio.run(server.call_tool('get_learning_dashboard', {'context_id': value}))
    reader.get.assert_not_called()
