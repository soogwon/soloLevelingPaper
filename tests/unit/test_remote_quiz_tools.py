"""원격 퀴즈는 생성과 동의 기록을 노출하지 않고 소유한 시도만 조회한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from tests.unit.test_remote_annotation_tools import Server,token
from solo_leveling.interfaces.mcp import remote_quiz as module


@pytest.mark.parametrize('value',[None,token([]),token(['questions:write']),token(['quizzes:read'],True)])
def test_other_scope_does_not_read_quiz(monkeypatch,value):
    monkeypatch.setattr(module,'get_access_token',lambda:value)
    server,service=Server(),Mock()
    module.register_remote_quiz(server,NS(owner=lambda:'alice'),service)
    assert server.tools['get_quiz']('ctx','quiz')['status']=='not_authorized'
    service.get.assert_not_called()


def test_read_only_and_wrong_review_are_pinned_to_saved_attempt(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['quizzes:read']))
    server,service=Server(),Mock()
    saved={'attempt_id':'fixed','results':[{'is_correct':True},{'is_correct':False,'evidence_ids':['old']}]}
    service.attempt.return_value=saved
    module.register_remote_quiz(server,NS(owner=lambda:'alice'),service)
    result=server.tools['get_wrong_answer_review']('ctx','fixed')
    assert result['result']['results']==[saved['results'][1]]
    assert len(saved['results'])==2
    assert server.tools['submit_quiz']('ctx','quiz','key',{'0':1},'consent')['status']=='not_authorized'
    service.submit.assert_not_called()
    assert not any('generate' in name or 'consent' in name for name in server.tools)


def test_position_keys_are_canonical_and_values_are_not_coerced(monkeypatch):
    monkeypatch.setattr(module,'get_access_token',lambda:token(['quizzes:write']))
    server,service=Server(),Mock()
    module.register_remote_quiz(server,NS(owner=lambda:'alice'),service)
    for answers in ({'00':1},{'-1':1},{'０':1},{'100':1},{}):
        assert server.tools['submit_quiz']('ctx','quiz','key',answers,'consent')['status']=='invalid_argument'
    service.submit.assert_not_called()
    service.submit.return_value={'attempt_id':'one'}
    server.tools['submit_quiz']('ctx','quiz','key',{'0':True},'consent')
    assert service.submit.call_args.args==('alice','ctx','quiz','key',{0:True},'consent')
    assert type(service.submit.call_args.args[4][0]) is bool


def test_actual_mcp_does_not_convert_boolean_answer_to_integer(monkeypatch):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    monkeypatch.setattr(module,'get_access_token',lambda:token(['quizzes:write']))
    server,service=FastMCP('합성 퀴즈'),Mock()
    service.submit.side_effect=ValueError('remote_quiz_answers_invalid')
    module.register_remote_quiz(server,NS(owner=lambda:'alice'),service)
    async def check():
        await server.call_tool('submit_quiz',{'context_id':'ctx','quiz_id':'quiz','request_key':'key',
                                             'answers':{'0':True},'storage_consent_id':'consent'})
        assert type(service.submit.call_args.args[4][0]) is bool
    asyncio.run(check())
