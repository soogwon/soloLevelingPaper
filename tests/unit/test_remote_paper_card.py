"""전체 상세 카드는 읽기 권한·연결 지문과 원격 목록 경계를 유지한다."""
from dataclasses import dataclass
from types import SimpleNamespace as NS
from unittest.mock import Mock
from tests.unit.test_remote_annotation_tools import Server, token, CONTEXT
from solo_leveling.interfaces.mcp import remote_annotations as module


@dataclass
class Note:
    annotation_id: str = CONTEXT
    context_id: str = CONTEXT
    body: str = '근거 없는 메모'


def test_whole_detail_includes_unlinked_notes_and_rejects_changed_connection(monkeypatch):
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read']))
    queries = NS(owner=lambda: 'alice', detail=Mock(return_value={'status':'ok','result':{'context_id':CONTEXT}}))
    service = Mock(service_id='remote-paper')
    service.store.list_annotations.return_value = {'items':[Note()],'next_cursor':None}
    server=Server();module.register_annotation_tools(server,queries,service)
    call=server.tools['show_remote_paper_detail']
    result=call(CONTEXT)
    assert result['status']=='ok'
    assert result['result']['annotation_page']['items'][0]['body']=='근거 없는 메모'
    service.store.list_annotations.assert_called_once_with('alice',CONTEXT,after=None,limit=20)
    queries.detail.reset_mock();service.store.list_annotations.reset_mock()
    assert call(CONTEXT,expected_binding='wrong')['result']['connection_changed']
    queries.detail.assert_not_called();service.store.list_annotations.assert_not_called()
    service.create.assert_not_called()


def test_whole_detail_denies_scope_and_missing_context(monkeypatch):
    queries=NS(owner=lambda:'alice',detail=Mock(return_value={'status':'not_found'}))
    service=Mock(service_id='remote-paper');server=Server()
    module.register_annotation_tools(server,queries,service)
    monkeypatch.setattr(module,'get_access_token',lambda:token([]))
    assert server.tools['show_remote_paper_detail'](CONTEXT)['status']=='not_authorized'
    queries.detail.assert_not_called()
    monkeypatch.setattr(module,'get_access_token',lambda:token(['annotations:read']))
    assert server.tools['show_remote_paper_detail'](CONTEXT)['status']=='not_found'
    service.store.list_annotations.assert_not_called()


def test_detail_and_direct_summary_share_read_result_and_scope(monkeypatch):
    from solo_leveling.interfaces.mcp import remote_summaries

    access = token(['annotations:read'])
    monkeypatch.setattr(module, 'get_access_token', lambda: access)
    monkeypatch.setattr(remote_summaries, 'get_access_token', lambda: access)
    queries = NS(owner=lambda: 'alice', detail=Mock(return_value={'status': 'ok', 'result': {}}))
    annotations = Mock(service_id='remote-paper')
    annotations.store.list_annotations.return_value = {'items': [], 'next_cursor': None}
    summaries = Mock()
    server = Server()
    module.register_annotation_tools(server, queries, annotations)
    assert server.tools['show_remote_paper_detail'](CONTEXT)['result']['saved_summary'] == {'status': 'unsupported'}

    latest = remote_summaries.register_summary_tools(server, queries, summaries)
    module.register_annotation_tools(server, queries, annotations, latest_summary=latest)
    detail = server.tools['show_remote_paper_detail']
    assert detail(CONTEXT)['result']['saved_summary'] == {'status': 'not_authorized'}
    summaries.latest.assert_not_called()

    access.scopes.append('summaries:write')
    summaries.latest.return_value = None
    assert detail(CONTEXT)['result']['saved_summary'] == latest(CONTEXT) == {'status': 'not_found'}
    summaries.latest.assert_called_with('alice', CONTEXT)

    summaries.latest.side_effect = RuntimeError('비공개 내부 오류')
    response = detail(CONTEXT)
    assert response['status'] == 'ok'
    assert response['result']['saved_summary']['status'] == 'unavailable'
    assert '비공개' not in str(response)
    summaries.prepare.assert_not_called()
    summaries.submit.assert_not_called()
