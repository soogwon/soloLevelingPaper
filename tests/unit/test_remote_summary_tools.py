"""요약 전용 권한과 검증 owner만 제품 서비스에 전달한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import asyncio
import pytest
from mcp.server.fastmcp import FastMCP
from solo_leveling.interfaces.mcp import remote_summaries as module
from tests.unit.test_remote_annotation_tools import Server, token


@pytest.mark.parametrize('value', [None, token([]), token(['questions:write']), token(['summaries:write'], True)])
def test_summary_scope_is_separate(monkeypatch, value):
    monkeypatch.setattr(module, 'get_access_token', lambda: value)
    server, service, queries = Server(), Mock(), NS(owner=Mock(return_value='alice'))
    module.register_summary_tools(server, queries, service)
    assert server.tools['prepare_remote_summary']('key','context')['status'] == 'not_authorized'
    queries.owner.assert_not_called()
    service.prepare.assert_not_called()


def test_read_is_not_resubmit_and_errors_are_safe(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['summaries:write']))
    server, service = Server(), Mock()
    module.register_summary_tools(server,NS(owner=lambda:'alice'),service)
    service.view.return_value = {'state':'queued'}
    assert server.tools['get_remote_summary_review']('key') == {'state':'queued'}
    service.view.assert_called_once_with('alice','key')
    service.submit.assert_not_called()
    service.view.side_effect = RuntimeError('비밀 원문')
    assert '비밀' not in str(server.tools['get_remote_summary_review']('key'))
    assert not any('consent' in name for name in server.tools)


def test_real_tools_do_not_accept_claimed_owner_or_consent(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['summaries:write']))
    server = FastMCP('합성 요약')
    service = Mock()
    module.register_summary_tools(server,NS(owner=lambda:'alice'),service)
    async def check():
        tools = await server.list_tools()
        assert len(tools) == 8
        for tool in tools:
            properties = tool.inputSchema['properties']
            assert 'owner_user_id' not in properties and 'confirmed' not in properties
    asyncio.run(check())


def test_summary_scope_is_supported_but_not_required_for_existing_readers():
    from starlette.testclient import TestClient
    from solo_leveling.interfaces.http.remote_mcp import create_remote_mcp
    from solo_leveling.interfaces.http.settings import RemoteMcpSettings
    settings = RemoteMcpSettings(enabled=True, product_reads_enabled=True,
        resource_url='https://mcp.example.com/mcp', audience='https://mcp.example.com/mcp',
        issuer='https://auth.example.com/', jwks_url='https://auth.example.com/jwks',
        allowed_hosts=('mcp.example.com',), allowed_client_ids=('synthetic',))
    for enabled in (False, True):
        bundle = create_remote_mcp(settings,product_queries=NS(),summary_service=NS() if enabled else None)
        assert 'summaries:write' not in bundle.server.settings.auth.required_scopes
        with TestClient(bundle.app,base_url='https://mcp.example.com') as client:
            result = client.get('/.well-known/oauth-protected-resource/mcp')
            assert result.status_code == 200
            assert ('summaries:write' in result.json()['scopes_supported']) is enabled


def test_export_uses_owner_and_same_key_without_generation(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['summaries:write']))
    server,service=Server(),Mock()
    module.register_summary_tools(server,NS(owner=lambda:'alice'),service)
    service.result.return_value={'status':'not_found'}
    assert server.tools['export_remote_summary']('key')=={'status':'not_found'}
    service.result.assert_called_once_with('alice','key')
    service.submit.assert_not_called()
