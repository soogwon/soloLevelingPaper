"""#399 제품 연결 전 합성 스파이크의 허용 목록·세션·롤백 경계를 고정한다."""

import time

from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken
import pytest

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.settings import RemoteMcpSettings, load_remote_mcp_settings

RESOURCE = 'https://service.example/mcp'
SETTINGS = load_remote_mcp_settings({
    'REMOTE_MCP_SPIKE_ENABLED': 'true',
    'REMOTE_MCP_OIDC_ISSUER': 'https://identity.example/',
    'REMOTE_MCP_OIDC_AUDIENCE': RESOURCE,
    'REMOTE_MCP_RESOURCE_URL': RESOURCE,
    'REMOTE_MCP_OIDC_JWKS_URL': 'https://identity.example/jwks',
    'REMOTE_MCP_ALLOWED_HOSTS': 'service.example',
    'REMOTE_MCP_ALLOWED_CLIENT_IDS': 'test-client',
})


class Verifier:
    async def verify_token(self, token):
        if token not in ('account-a', 'account-b'):
            return None
        return AccessToken(token=token, client_id='test-client', scopes=['spike:read'],
                           subject=token, resource=RESOURCE, expires_at=int(time.time())+60)


def call(client, method, params=None, token='account-a', **headers):
    return client.post('/mcp', headers={'Authorization':f'Bearer {token}',
        'Accept':'application/json, text/event-stream', **headers},
        json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})


@pytest.fixture
def client():
    with TestClient(create_app(remote_mcp_settings=SETTINGS, remote_mcp_verifier=Verifier()),
                    base_url='https://service.example') as value:
        yield value


@pytest.mark.parametrize('name', ['add_paper', 'open_pdf', 'submit_paper_question',
                                 'ask_paper', 'delete_paper', 'unknown_tool'])
def test_local_paid_and_unapproved_tools_are_not_exposed(client, name):
    tools = call(client, 'tools/list').json()['result']['tools']
    assert {tool['name'] for tool in tools} == {'whoami', 'probe_card', 'probe_delay'}
    response = call(client, 'tools/call', {'name':name, 'arguments':{}})
    assert response.json()['result']['isError'] is True


def test_connection_does_not_authorize_later_invalid_token(client):
    assert call(client, 'tools/list').status_code == 200
    assert call(client, 'tools/list', token='expired-or-invalid').status_code == 401
    assert call(client, 'tools/call', {'name':'whoami'}, token='account-b').status_code == 200


@pytest.mark.parametrize('account', ['account-a', 'account-b'])
def test_stateless_spike_never_treats_session_id_as_authority(client, account):
    response = call(client, 'tools/list', token=account, **{'Mcp-Session-Id':'copied-session'})
    assert response.status_code == 400
    assert response.json() == {'error':'spike_sessions_not_supported'}


def test_flag_off_closes_endpoint_and_metadata_without_breaking_health():
    with TestClient(create_app(remote_mcp_settings=RemoteMcpSettings())) as client:
        assert client.post('/mcp').status_code == 404
        metadata = client.get('/.well-known/oauth-protected-resource/mcp')
        assert metadata.status_code == 404
        assert client.get('/health').json()['status'] == 'ok'
        assert client.get('/v1/session').status_code == 401
