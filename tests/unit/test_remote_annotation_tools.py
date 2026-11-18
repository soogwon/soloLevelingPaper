"""검증 identity와 scope가 없으면 원격 메모 저장소를 호출하지 않는다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time

import pytest
from solo_leveling.interfaces.mcp import remote_annotations as module
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity

CONTEXT = '00000000-0000-0000-0000-000000000580'


class Server:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def token(scopes, expired=False):
    result = RemoteIdentityAccessToken(token='synthetic', client_id='client', scopes=scopes,
                                      expires_at=int(time.time()) + (-1 if expired else 60))
    result._verified_identity = VerifiedRemoteIdentity('https://issuer.example/', 'alice')
    return result


@pytest.mark.parametrize('value', [None, token([]), token(['annotations:write'], True)])
def test_missing_scope_or_expiry_never_calls_store(monkeypatch, value):
    monkeypatch.setattr(module, 'get_access_token', lambda: value)
    server = Server(); service = Mock(); queries = NS(owner=Mock(return_value='alice'))
    module.register_annotation_tools(server, queries, service)
    assert server.tools['start_remote_learning'](CONTEXT)['status'] == 'not_authorized'
    queries.owner.assert_not_called()
    service.learning.start.assert_not_called()
    assert not any('consent' in name for name in server.tools)


def test_verified_owner_and_safe_failure(monkeypatch):
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:write']))
    server = Server(); service = Mock(); queries = NS(owner=lambda: 'verified-owner')
    module.register_annotation_tools(server, queries, service)
    service.create.return_value = {'annotation': 'synthetic'}
    result = server.tools['create_remote_annotation'](CONTEXT, 'key', {'kind': 'note', 'body': '메모'})
    assert result['status'] == 'ok'
    assert service.create.call_args.args[:3] == ('verified-owner', CONTEXT, 'key')
    service.create.side_effect = RuntimeError('비밀 내용')
    result = server.tools['create_remote_annotation'](CONTEXT, 'key', {'kind': 'note', 'body': '메모'})
    assert result['status'] == 'unavailable' and '비밀' not in str(result)
    assert server.tools['create_remote_annotation'](CONTEXT, 'key', {'kind': 'note', 'body': '메모', 'owner_user_id': 'forged'})['status'] == 'invalid_argument'


def test_read_scope_cannot_write(monkeypatch):
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read']))
    server = Server(); service = Mock(); queries = NS(owner=lambda: 'alice')
    module.register_annotation_tools(server, queries, service)
    service.lookup.return_value = {'state': 'deleted'}
    assert server.tools['lookup_remote_annotation_request'](CONTEXT, 'key')['status'] == 'ok'
    assert server.tools['delete_remote_annotation'](CONTEXT, CONTEXT, 1)['status'] == 'not_authorized'
    service.store.delete_annotation.assert_not_called()
