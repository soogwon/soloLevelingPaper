"""상태 UI 메타데이터·리소스와 기존 텍스트 계약을 검증한다."""

import asyncio
import json
from unittest.mock import Mock, call

import pytest

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
        resources = [r for r in resources if str(r.uri) == STATUS_APP_URI]
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


@pytest.mark.parametrize('fields', [
    {},
    dict(failed_stage='translate', failure_code='provider_unavailable', retryable=None,
         next_action=None, outcome='partial'),
    dict(failed_stage='parse', failure_code='pdf_encrypted', retryable=False,
         next_action='replace_file', outcome='failed'),
    dict(failed_stage='translate', failure_code='translation_failed', retryable=True,
         next_action='register_again', outcome='failed'),
])
def test_status_text_and_structured_response_preserve_service_fields(fields):
    ingestion = Mock()
    ingestion.get_status.return_value = {
        'job_id': 'job', 'version_id': 'version', 'status': 'ready', 'stage': 'index',
        'limitations': ['translation_failed:chunk:provider_unavailable'],
        'result_available': True, **fields,
    }
    ready = fields.get('outcome') != 'failed'
    if not ready:
        ingestion.get_status.return_value.update(status='failed', stage=fields['failed_stage'],
                                                 limitations=[fields['failure_code']], result_available=False)
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    content, structured = asyncio.run(server.call_tool('get_paper_status', {'job_id': 'job'}))
    empty = dict.fromkeys(('failed_stage', 'failure_code', 'retryable', 'next_action', 'outcome'))
    extra = {'translation_recovery_notice': None}
    if fields.get('outcome') == 'partial':
        notice = structured['translation_recovery_notice']
        for phrase in ('신청할 수 없습니다', '원문', '번역 API', '비용', '별도 동의', '기존 학습 맥락'):
            assert phrase in notice
        extra['translation_recovery_notice'] = notice
    assert structured == {**empty, **extra, **ingestion.get_status.return_value,
                          'capabilities': ['start_learning'] if ready else []}
    assert json.loads(content[0].text) == structured
    assert ingestion.mock_calls == [call.get_status('job')]


def test_no_status_resource_without_ingestion():
    server = create_server(MCPServices(Mock(), Mock()))
    assert all(str(r.uri) != STATUS_APP_URI for r in asyncio.run(server.list_resources()))
