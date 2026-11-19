"""검토 링크는 생성·동의를 실행하지 않고 검증된 owner에만 연결한다."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from mcp.server.fastmcp import FastMCP
from tests.unit.test_remote_annotation_tools import Server, token
from solo_leveling.interfaces.mcp import remote_quiz_reviews as module


@pytest.mark.parametrize('credential',[None,token([]),token(['quizzes:read']),token(['quizzes:write'],True)])
def test_scope_required_before_owner_or_store(monkeypatch,credential):
    monkeypatch.setattr(module,'get_access_token',lambda:credential)
    server,service,queries=Server(),Mock(),NS(owner=Mock(return_value='alice'))
    module.register_quiz_review_tools(server,queries,service)
    assert server.tools['get_remote_quiz_generation_review']('key')['status']=='not_authorized'
    queries.owner.assert_not_called();service.view.assert_not_called()


def test_preparation_and_lookup_never_grant_or_submit(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['quizzes:write']))
    server,service=Server(),Mock()
    module.register_quiz_review_tools(server,NS(owner=lambda:'alice'),service)
    server.tools['prepare_remote_quiz_generation']('key','context',['evidence'],'preview',2,'multiple_choice')
    service.prepare.assert_called_once_with('alice','key','context',('evidence',),step='preview',question_count=2,question_type='multiple_choice')
    server.tools['get_remote_quiz_generation_review']('key')
    service.view.assert_called_once_with('alice','key')
    service.grant.assert_not_called();service.submit.assert_not_called()
    service.view.side_effect=RuntimeError('비밀 원문')
    assert '비밀' not in str(server.tools['get_remote_quiz_generation_review']('key'))


def test_actual_tool_schema_excludes_owner_consent_and_submit():
    server=FastMCP('합성 퀴즈 검토')
    module.register_quiz_review_tools(server,NS(owner=lambda:'alice'),Mock())
    tools=asyncio.run(server.list_tools())
    assert len(tools)==2
    for tool in tools:
        assert not {'owner_user_id','confirmed','consent_id'} & tool.inputSchema['properties'].keys()
        assert 'submit' not in tool.name


def test_optional_scope_is_not_required_for_existing_readers():
    from starlette.testclient import TestClient
    from solo_leveling.interfaces.http.remote_mcp import create_remote_mcp
    from solo_leveling.interfaces.http.settings import RemoteMcpSettings
    settings=RemoteMcpSettings(enabled=True,product_reads_enabled=True,resource_url='https://mcp.example.com/mcp',
        audience='https://mcp.example.com/mcp',issuer='https://auth.example.com/',jwks_url='https://auth.example.com/jwks',
        allowed_hosts=('mcp.example.com',),allowed_client_ids=('synthetic',))
    for enabled in (False,True):
        bundle=create_remote_mcp(settings,product_queries=NS(),quiz_review_service=NS() if enabled else None)
        assert 'quizzes:write' not in bundle.server.settings.auth.required_scopes
        with TestClient(bundle.app,base_url='https://mcp.example.com') as client:
            data=client.get('/.well-known/oauth-protected-resource/mcp').json()
            assert ('quizzes:write' in data['scopes_supported']) is enabled
