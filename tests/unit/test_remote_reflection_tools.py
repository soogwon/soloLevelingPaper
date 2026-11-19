"""원격 성찰 권한 분리와 안전한 고정 오류를 검증한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from tests.unit.test_remote_annotation_tools import Server,token
from solo_leveling.interfaces.mcp import remote_reflection as module


@pytest.mark.parametrize('value',[None,token([]),token(['annotations:write']),token(['reflections:write'],True)])
def test_wrong_scope_or_expiry_does_not_access_service(monkeypatch,value):
    monkeypatch.setattr(module,'get_access_token',lambda:value)
    server,service=Server(),Mock()
    module.register_remote_reflection(server,NS(owner=lambda:'alice'),service)
    assert server.tools['get_reading_questions']('ctx')['status']=='not_authorized'
    service.questions.assert_not_called()


def test_read_cannot_write_or_grant_consent(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['reflections:read']))
    server,service=Server(),Mock()
    service.comparison.return_value={'groups':[]}
    module.register_remote_reflection(server,NS(owner=lambda:'alice'),service)
    assert server.tools['get_reading_comparison']('ctx')['result']=={'groups':[]}
    assert server.tools['delete_reading_answer']('ctx','q',1)['status']=='not_authorized'
    service.delete_answer.assert_not_called()
    assert not any(name.startswith('grant') for name in server.tools)
    service.comparison.side_effect=RuntimeError('비밀 답변')
    assert '비밀' not in str(server.tools['get_reading_comparison']('ctx'))


def test_summary_and_reflection_scopes_coexist_without_blocking_readers():
    from starlette.testclient import TestClient
    from solo_leveling.interfaces.http.remote_mcp import create_remote_mcp
    from solo_leveling.interfaces.http.settings import RemoteMcpSettings
    settings=RemoteMcpSettings(enabled=True,product_reads_enabled=True,
        resource_url='https://mcp.example.com/mcp',audience='https://mcp.example.com/mcp',
        issuer='https://auth.example.com/',jwks_url='https://auth.example.com/jwks',
        allowed_hosts=('mcp.example.com',),allowed_client_ids=('synthetic',))
    bundle=create_remote_mcp(settings,product_queries=NS(),summary_service=NS(),reflection_service=NS())
    assert set(bundle.server.settings.auth.required_scopes)=={'spike:read','papers:read'}
    with TestClient(bundle.app,base_url='https://mcp.example.com') as client:
        scopes=client.get('/.well-known/oauth-protected-resource/mcp').json()['scopes_supported']
        assert {'summaries:write','reflections:read','reflections:write'}<=set(scopes)
