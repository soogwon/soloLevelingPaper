"""카드의 인증 연결·같은 키 조회·제출 전 정답 비노출 계약."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4
import asyncio
from mcp.server.fastmcp import FastMCP
from tests.unit.test_remote_annotation_tools import Server, token
from solo_leveling.interfaces.mcp import remote_quiz as module


def setup(monkeypatch):
    current=token(['quizzes:read','quizzes:write'])
    monkeypatch.setattr(module,'get_access_token',lambda:current)
    service=Mock();server=Server()
    ctx,quiz,key=str(uuid4()),str(uuid4()),str(uuid4())
    service.get.return_value={'context_id':ctx,'quiz_id':quiz,'questions':[]}
    service.consent.return_value={'storage':'current','consent_id':'synthetic'}
    service.submit.return_value={'quiz_id':quiz}
    module.register_remote_quiz(server,NS(owner=lambda:'alice'),service)
    view=server.tools['show_remote_quiz'](ctx,quiz)
    return server,service,current,ctx,quiz,key,view['result']['binding']


def test_token_change_blocks_card_submission_and_recovery(monkeypatch):
    server,service,current,ctx,quiz,key,bound=setup(monkeypatch)
    current.token='changed-synthetic-token'
    assert server.tools['submit_remote_quiz_card'](ctx,quiz,key,{'0':1},'synthetic',bound)['status']=='invalid_argument'
    assert server.tools['get_remote_quiz_submission'](ctx,quiz,key,bound)['status']=='invalid_argument'
    service.submit.assert_not_called();service.attempt_by_key.assert_not_called()


def test_card_never_grants_consent_and_preserves_same_key(monkeypatch):
    server,service,_,ctx,quiz,key,bound=setup(monkeypatch)
    assert server.tools['submit_remote_quiz_card'](ctx,quiz,key,{'0':1},'synthetic',bound)['status']=='ok'
    service.submit.assert_called_once_with('alice',ctx,quiz,key,{0:1},'synthetic')
    service.grant_storage.assert_not_called()
    service.attempt_by_key.return_value={'context_id':ctx,'quiz_id':quiz,'request_key':key,'attempt':None}
    assert server.tools['get_remote_quiz_submission'](ctx,quiz,key,bound)['result']['attempt'] is None
    assert service.submit.call_count==1


def test_real_fastmcp_card_resources_and_hidden_write_tool(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['quizzes:read']))
    server=FastMCP('합성 퀴즈 카드');module.register_remote_quiz(server,NS(owner=lambda:'alice'),Mock())
    async def run():
        tools={tool.name:tool for tool in await server.list_tools()}
        assert tools['show_remote_quiz'].meta['ui']['resourceUri']=='ui://remote-quiz/attempt-v2.html'
        assert tools['submit_remote_quiz_card'].meta['ui']['visibility']==['app']
        resources=await server.list_resources()
        assert any(str(r.uri)=='ui://remote-quiz/attempt-v2.html' for r in resources)
    asyncio.run(run())
