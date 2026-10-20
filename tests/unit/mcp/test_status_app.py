"""상태 UI 메타데이터·리소스와 기존 텍스트 계약을 검증한다."""

import asyncio
import json
from unittest.mock import Mock

from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.interfaces.mcp.status_app import STATUS_APP_MIME, STATUS_APP_URI


def test_status_app_resource_and_tool_link():
    server = create_server(MCPServices(Mock(), Mock(), Mock()))

    async def scenario():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['get_paper_status'].meta == {'ui': {'resourceUri': STATUS_APP_URI}}
        assert tools['get_paper_status'].annotations.readOnlyHint is True
        assert tools['get_paper_status'].annotations.openWorldHint is False
        resources = await server.list_resources()
        assert len(resources) == 1
        assert str(resources[0].uri) == STATUS_APP_URI
        assert resources[0].mimeType == STATUS_APP_MIME
        contents = list(await server.read_resource(STATUS_APP_URI))
        assert len(contents) == 1
        assert contents[0].mime_type == STATUS_APP_MIME
        assert contents[0].meta['ui']['csp'] == {'connectDomains': [], 'resourceDomains': []}
        html = contents[0].content
        assert '<html lang="ko">' in html
        assert '논문 처리 상태' in html
        assert '<!-- APP_SCRIPT -->' not in html
        assert '<script src=' not in html
        assert '<script>' in html

    asyncio.run(scenario())


def test_status_text_fallback_is_unchanged_without_ui_negotiation():
    ingestion = Mock()
    ingestion.get_status.return_value = {
        'job_id': 'job', 'version_id': 'version', 'status': 'ready', 'stage': 'index',
        'limitations': ['translation_failed:chunk:provider_unavailable'],
        'result_available': True,
    }
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    content, structured = asyncio.run(server.call_tool('get_paper_status', {'job_id': 'job'}))
    assert structured == {**ingestion.get_status.return_value, 'capabilities': ['start_learning']}
    assert json.loads(content[0].text) == structured
    ingestion.get_status.assert_called_once_with('job')


def test_no_status_resource_without_ingestion():
    server = create_server(MCPServices(Mock(), Mock()))
    assert asyncio.run(server.list_resources()) == []
