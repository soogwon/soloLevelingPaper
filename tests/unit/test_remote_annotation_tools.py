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

    def resource(self, *args, **kwargs):
        return lambda fn: fn

    def tool(self, **kwargs):
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


def test_real_mcp_registration_serializes_nested_records(monkeypatch):
    import asyncio
    from dataclasses import dataclass
    from mcp.server.fastmcp import FastMCP

    @dataclass
    class Record:
        annotation_id: str
        body: str

    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read']))
    server = FastMCP('합성 검증')
    service = Mock()
    service.store.list_annotations.return_value = {'items': [Record(CONTEXT, '합성 메모')], 'next_cursor': None}
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), service)

    async def check():
        names = {tool.name for tool in await server.list_tools()}
        assert 'list_remote_annotations' in names
        assert len(names) == 14
        result = await server.call_tool('list_remote_annotations', {'context_id': CONTEXT})
        assert '합성 메모' in str(result)
        assert 'annotation_id' in str(result)

    asyncio.run(check())


def test_card_confirmation_bound_to_authenticated_token(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class Recovery:
        request_key: str = 'existing'
        state: str = 'committed'

    current = token(['annotations:read', 'annotations:write'])
    monkeypatch.setattr(module, 'get_access_token', lambda: current)
    service = Mock(service_id='remote-paper')
    service.lookup.return_value = Recovery()
    service.confirm.return_value = True
    server = Server()
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), service)
    show = server.tools['show_remote_annotation_recovery']
    confirm = server.tools['confirm_remote_annotation_card']
    result = show(CONTEXT, 'existing')['result']
    bound = result['binding']
    assert len(bound) == 64 and 'synthetic' not in str(result)
    assert confirm(CONTEXT, 'existing', CONTEXT, 1, bound)['result']['confirmed']
    service.confirm.assert_called_once_with('alice', CONTEXT, 'existing', CONTEXT, 1)
    service.confirm.reset_mock()
    current.token = 'renewed-synthetic'
    assert show(CONTEXT, 'existing', bound)['result']['connection_changed']
    assert confirm(CONTEXT, 'existing', CONTEXT, 1, bound)['result']['connection_changed']
    service.confirm.assert_not_called()
    assert show(CONTEXT, 'existing')['result']['binding'] != bound


def test_recovery_resource_and_tool_metadata(monkeypatch):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    server = FastMCP('합성 카드')
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), Mock())

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['show_remote_annotation_recovery'].meta['ui']['resourceUri'] == 'ui://remote-annotations/recovery.html'
        assert tools['show_remote_annotation_recovery'].annotations.readOnlyHint
        assert tools['confirm_remote_annotation_card'].meta['ui']['visibility'] == ['app']
        resource = await server.read_resource('ui://remote-annotations/recovery.html')
        assert '원격 메모 복구 확인' in str(resource)

    asyncio.run(check())


def test_prepare_card_only_recovers_and_save_requires_current_connection(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class Saved:
        request_key: str = 'existing'

    current = token(['annotations:read', 'annotations:write'])
    monkeypatch.setattr(module, 'get_access_token', lambda: current)
    service = Mock(service_id='remote-paper')
    service.learning.get.return_value = {'context_id': CONTEXT}
    service.recover.return_value = Saved()
    service.create.return_value = Saved()
    server = Server()
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), service)
    value = {'kind': 'note', 'body': '합성'}
    result = server.tools['prepare_remote_annotation'](CONTEXT, value)['result']
    assert result['value'] == value and result['existing']['request_key'] == 'existing'
    service.create.assert_not_called()
    bound = result['binding']
    current.token = 'other-token'
    assert server.tools['save_remote_annotation_card'](CONTEXT, 'existing', value, bound)['result']['connection_changed']
    service.create.assert_not_called()
    current.token = 'synthetic'
    assert server.tools['save_remote_annotation_card'](CONTEXT, 'existing', value, bound)['status'] == 'ok'
    service.create.assert_called_once()


def test_prepare_card_missing_learning_never_recovers(monkeypatch):
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read']))
    service = Mock(service_id='remote-paper')
    service.learning.get.return_value = None
    server = Server()
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), service)
    assert server.tools['prepare_remote_annotation'](CONTEXT, {'kind':'note', 'body':'합성'})['status'] == 'not_found'
    service.recover.assert_not_called()
    service.learning.start.assert_not_called()


def test_compose_tools_return_structured_content_in_actual_sdk(monkeypatch):
    import asyncio
    from dataclasses import dataclass
    from mcp.server.fastmcp import FastMCP

    @dataclass
    class Saved:
        request_key: str = 'existing'

    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read', 'annotations:write']))
    service = Mock(service_id='remote-paper')
    service.learning.get.return_value = {'context_id': CONTEXT}
    service.recover.return_value = None
    service.create.return_value = Saved()
    server = FastMCP('작성 카드 구조화 응답 검증')
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), service)

    async def check():
        value = {'kind': 'note', 'body': '합성 메모'}
        prepared = await server.call_tool('prepare_remote_annotation', {'context_id': CONTEXT, 'value': value})
        assert isinstance(prepared, tuple)
        assert prepared[1]['result']['existing'] is None
        service.create.assert_not_called()
        saved = await server.call_tool('save_remote_annotation_card', {
            'context_id': CONTEXT, 'request_key': 'existing', 'value': value,
            'expected_binding': prepared[1]['result']['binding']})
        assert isinstance(saved, tuple)
        assert saved[1] == {'status': 'ok', 'result': {'request_key': 'existing'}}
        metadata = {tool.name: tool for tool in await server.list_tools()}
        assert metadata['prepare_remote_annotation'].outputSchema is not None
        assert metadata['save_remote_annotation_card'].outputSchema is not None
        assert metadata['save_remote_annotation_card'].meta['ui']['visibility'] == ['app']

    asyncio.run(check())
