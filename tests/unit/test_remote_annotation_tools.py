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
        assert 'show_remote_paper_detail' in names
        assert {'show_remote_reading_progress', 'save_remote_reading_progress_card'} <= names
        assert len(names) == 23
        assert 'prepare_remote_highlight_from_quote' in names
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


def test_card_tools_have_actual_structured_mcp_outputs(monkeypatch):
    import asyncio
    from dataclasses import dataclass
    from mcp.server.fastmcp import FastMCP

    @dataclass
    class Recovery:
        request_key: str = 'existing'
        state: str = 'committed'

    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read', 'annotations:write']))
    service = Mock(service_id='remote-paper')
    service.lookup.return_value = Recovery()
    service.confirm.return_value = True
    server = FastMCP('구조화 응답 검증')
    module.register_annotation_tools(server, NS(owner=lambda: 'alice'), service)

    async def check():
        result = await server.call_tool('show_remote_annotation_recovery',
                                       {'context_id': CONTEXT, 'request_key': 'existing'})
        assert isinstance(result, tuple), '텍스트만 반환하면 카드가 결과를 해석하지 못한다'
        text, structured = result
        assert structured['status'] == 'ok'
        assert structured['result']['context_id'] == CONTEXT
        assert text[0].type == 'text'
        confirmation = await server.call_tool('confirm_remote_annotation_card', {
            'context_id': CONTEXT, 'request_key': 'existing', 'annotation_id': CONTEXT,
            'revision': 1, 'expected_binding': structured['result']['binding']})
        assert isinstance(confirmation, tuple)
        assert confirmation[1] == {'status': 'ok', 'result': {'confirmed': True}}
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['show_remote_annotation_recovery'].outputSchema is not None
        assert tools['confirm_remote_annotation_card'].outputSchema is not None

    asyncio.run(check())


def test_evidence_card_scopes_binding_and_serialization(monkeypatch):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    current = token(['annotations:read'])
    monkeypatch.setattr(module, 'get_access_token', lambda: current)
    service = Mock(service_id='remote-paper')
    service.store.list_annotations.return_value = {'items': [], 'next_cursor': None}
    queries = NS(owner=lambda: 'alice', evidences=Mock())
    queries.evidences.get.return_value = {'context_id': CONTEXT, 'evidence_id': CONTEXT}
    server = FastMCP('근거 카드 검증')
    module.register_annotation_tools(server, queries, service)
    async def check():
        args = {'context_id': CONTEXT, 'evidence_id': CONTEXT}
        result = await server.call_tool('show_remote_evidence_detail', args)
        assert result[1]['status'] == 'ok'
        bound = result[1]['result']['binding']
        queries.evidences.get.assert_called_once_with('alice', CONTEXT, CONTEXT)
        service.store.list_annotations.assert_called_once_with('alice', CONTEXT, after=None, limit=20, evidence=queries.evidences.get.return_value)
        queries.evidences.reset_mock(); service.store.reset_mock()
        current.token = 'renewed'
        changed = await server.call_tool('show_remote_evidence_detail', {**args, 'expected_binding': bound})
        assert changed[1]['result'] == {'connection_changed': True}
        queries.evidences.get.assert_not_called(); service.store.list_annotations.assert_not_called()
        current.scopes = []
        denied = await server.call_tool('list_remote_evidence_annotations', args)
        assert denied[1]['status'] == 'not_authorized'
        queries.evidences.get.assert_not_called()
        current.scopes = ['annotations:read']
        queries.evidences.get.return_value = None
        missing = await server.call_tool('list_remote_evidence_annotations', args)
        assert missing[1]['status'] == 'not_found'
        service.store.list_annotations.assert_not_called()
        tools = {t.name:t for t in await server.list_tools()}
        assert tools['show_remote_evidence_detail'].annotations.readOnlyHint
        assert tools['list_remote_evidence_annotations'].outputSchema
    asyncio.run(check())


def test_evidence_integrity_failure_is_unavailable_not_bad_input(monkeypatch):
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read']))
    service = Mock(service_id='remote-paper')
    queries = NS(owner=lambda: 'alice', evidences=Mock())
    queries.evidences.get.side_effect = ValueError('민감한 원문 오류')
    server = Server()
    module.register_annotation_tools(server, queries, service)
    result = server.tools['show_remote_evidence_detail'](CONTEXT, CONTEXT)
    assert result['status'] == 'unavailable' and '민감한' not in str(result)
    service.store.list_annotations.assert_not_called()


@pytest.mark.parametrize('failure', [FileNotFoundError, PermissionError])
def test_evidence_text_survives_card_resource_failure(monkeypatch, failure):
    """HTML을 읽지 못해도 텍스트로 근거·기록·다음 페이지를 조회한다."""
    import asyncio
    import json
    from dataclasses import dataclass
    from pathlib import Path
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ResourceError
    from solo_leveling.interfaces.mcp.remote_evidence_cards import CARD_URI

    @dataclass
    class Record:
        annotation_id: str
        body: str
        revision: int = 1

    current = token(['annotations:read'])
    monkeypatch.setattr(module, 'get_access_token', lambda: current)
    service = Mock(service_id='remote-paper')
    service.store.list_annotations.side_effect = [
        {'items': [Record(CONTEXT, '합성 첫 메모')], 'next_cursor': CONTEXT},
        {'items': [Record(CONTEXT, '합성 첫 메모')], 'next_cursor': CONTEXT},
        {'items': [Record('00000000-0000-0000-0000-000000000591', '합성 마지막 메모')], 'next_cursor': None},
    ]
    evidence = {'context_id': CONTEXT, 'evidence_id': CONTEXT,
                'quote_original': 'Synthetic evidence.', 'quote_ko': '합성 근거.',
                'version_id': 'fixed-version', 'publication_id': 'fixed-publication',
                'pdf_page': 1, 'page_notice': '고정 판본 기준'}
    queries = NS(owner=lambda: 'alice', evidences=Mock())
    queries.evidences.get.return_value = evidence
    server = FastMCP('카드 실패 합성 검증')
    module.register_annotation_tools(server, queries, service)
    original_read = Path.read_text

    def fail_card(path, *args, **kwargs):
        if path.name == 'remote-evidence-detail.html':
            raise failure('합성 리소스 실패')
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', fail_card)

    async def check():
        args = {'context_id': CONTEXT, 'evidence_id': CONTEXT}
        text, structured = await server.call_tool('show_remote_evidence_detail', args)
        with pytest.raises(ResourceError, match='합성 리소스 실패'):
            await server.read_resource(CARD_URI)
        again_text, again_result = await server.call_tool('show_remote_evidence_detail', args)
        assert json.loads(again_text[0].text) == again_result == structured
        fallback = json.loads(text[0].text)
        assert fallback == structured
        assert fallback['result']['evidence'] == evidence
        assert fallback['result']['annotation_page']['items'][0] == {
            'annotation_id': CONTEXT, 'body': '합성 첫 메모', 'revision': 1}
        cursor = fallback['result']['annotation_page']['next_cursor']
        following, result = await server.call_tool('list_remote_evidence_annotations', {**args, 'after': cursor})
        assert json.loads(following[0].text) == result
        assert result['result']['annotation_page']['items'][0]['body'] == '합성 마지막 메모'
        assert result['result']['annotation_page']['next_cursor'] is None
        assert service.store.list_annotations.call_args.kwargs['after'] == cursor
        service.create.assert_not_called()
        service.update.assert_not_called()
        service.delete.assert_not_called()
        reads_before_denial = service.store.list_annotations.call_count
        current.scopes = []
        denied, result = await server.call_tool('show_remote_evidence_detail', args)
        assert json.loads(denied[0].text) == result == {'status': 'not_authorized'}
        assert service.store.list_annotations.call_count == reads_before_denial

    asyncio.run(check())


def test_progress_requires_write_scope_and_maps_revision_conflict(monkeypatch):
    from solo_leveling.interfaces.http.remote_learning import RemoteLearningConflict
    value = token(['annotations:read'])
    monkeypatch.setattr(module, 'get_access_token', lambda: value)
    server = Server(); service = Mock()
    module.register_annotation_tools(server, NS(owner=lambda:'alice'), service)
    args = (CONTEXT, CONTEXT, 'completed', None, 1)
    assert server.tools['set_remote_reading_progress'](*args)['status']=='not_authorized'
    service.learning.set_progress.assert_not_called()
    value = token(['annotations:write'])
    service.learning.set_progress.side_effect = RemoteLearningConflict('비공개 내부 원인')
    result = server.tools['set_remote_reading_progress'](*args)
    assert result['status']=='conflict' and '비공개' not in str(result)


def test_progress_mcp_rejects_boolean_revision_before_store(monkeypatch):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:write']))
    server=FastMCP('엄격 입력 검사'); service=Mock()
    module.register_annotation_tools(server,NS(owner=lambda:'alice'),service)
    async def verify():
        with pytest.raises(ToolError):
            await server.call_tool('set_remote_reading_progress', dict(context_id=CONTEXT,
                request_key=CONTEXT,status='reading',pdf_page=None,expected_revision=True))
        service.learning.set_progress.assert_not_called()
    asyncio.run(verify())
