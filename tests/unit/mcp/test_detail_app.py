"""근거 상세 리소스와 기존 텍스트 응답의 호환성을 검증한다."""
import asyncio
import json
from unittest.mock import Mock
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.interfaces.mcp.detail_app import DETAIL_APP_URI, DETAIL_APP_MIME
from solo_leveling.domain.evidence_qa import EvidenceDetail, GetEvidenceResult


def test_detail_resource_links_both_tools_without_ingestion():
    server = create_server(MCPServices(Mock(), Mock()))
    async def scenario():
        tools = {t.name: t for t in await server.list_tools()}
        assert set(tools) == {'ask_paper', 'get_evidence'}
        for name in tools:
            assert tools[name].meta['ui']['resourceUri'] == DETAIL_APP_URI
        contents = list(await server.read_resource(DETAIL_APP_URI))
        assert contents[0].mime_type == DETAIL_APP_MIME
        assert contents[0].meta['ui']['csp'] == {'connectDomains': [], 'resourceDomains': []}
        html = contents[0].content
        assert '논문 근거 상세' in html and '<!-- APP_SCRIPT -->' not in html
        assert '<script src=' not in html
    asyncio.run(scenario())


def test_evidence_text_response_preserves_snapshot_and_missing_translation():
    evidence = Mock()
    detail = EvidenceDetail('e', 'c', 'v', 'p', 't', None, 7, None, 'Original.', 'paper.pdf')
    evidence.get.return_value = GetEvidenceResult((detail,))
    server = create_server(MCPServices(Mock(), evidence))
    content, data = asyncio.run(server.call_tool('get_evidence', {'context_id':'ctx','evidence_ids':['e']}))
    assert json.loads(content[0].text) == data
    assert data['evidence'][0]['quote_ko'] is None
    assert data['evidence'][0]['pdf_page'] == 7
    assert data['evidence'][0]['translation_revision_id'] == 't'
    evidence.get.assert_called_once_with('ctx',['e'])


def test_saved_detail_tool_is_readonly_and_preserves_json():
    detail = Mock()
    detail.get.return_value = {'summary': None, 'evidence': [], 'context_id': 'ctx', 'paper_id':'paper', 'title':None, 'file_display_name':'paper.pdf', 'version_id':'v', 'parse_revision_id':'p', 'translation_revision_id':'t', 'scope':'paper', 'section_ids':[], 'keywords':[], 'important_sentences':[], 'limitations':['summary_not_available']}
    server = create_server(MCPServices(Mock(), Mock(), detail=detail))
    async def scenario():
        tools = {t.name: t for t in await server.list_tools()}
        tool = tools['get_paper_detail']
        assert tool.meta['ui']['resourceUri'] == DETAIL_APP_URI
        assert tool.annotations.readOnlyHint is True
        content, data = await server.call_tool('get_paper_detail', {'context_id':'ctx'})
        assert json.loads(content[0].text) == data == detail.get.return_value
    asyncio.run(scenario())
    detail.get.assert_called_once_with('ctx', scope='paper', offset=0, limit=20, summary_id=None)
