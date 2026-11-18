"""메모 제품을 켰을 때만 연결 메타데이터에 필요한 OAuth 권한을 고지한다."""
from types import SimpleNamespace
from starlette.testclient import TestClient
from solo_leveling.interfaces.http.remote_mcp import create_remote_mcp
from solo_leveling.interfaces.http.settings import RemoteMcpSettings


def test_annotation_scopes_are_discoverable_only_when_enabled():
    settings = RemoteMcpSettings(enabled=True, product_reads_enabled=True,
        resource_url='https://mcp.example.com/mcp', audience='https://mcp.example.com/mcp',
        issuer='https://auth.example.com/', jwks_url='https://auth.example.com/jwks',
        allowed_hosts=('mcp.example.com',), allowed_client_ids=('synthetic',))
    for enabled in (False, True):
        bundle = create_remote_mcp(settings, product_queries=SimpleNamespace(),
            annotation_service=SimpleNamespace() if enabled else None)
        with TestClient(bundle.app, base_url='https://mcp.example.com') as client:
            response = client.get('/.well-known/oauth-protected-resource/mcp')
            assert response.status_code == 200
            scopes = set(response.json()['scopes_supported'])
            assert ('annotations:read' in scopes) is enabled
            assert ('annotations:write' in scopes) is enabled
            assert {'spike:read', 'papers:read'} <= scopes
