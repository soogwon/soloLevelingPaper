"""전용 설정·조립·풀 정리를 실제 DB와 API 없이 검증한다."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.remote_quiz_settings import load_quiz_settings
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.devices import PostgresDeviceStore
from solo_leveling.interfaces.http import remote_mcp


def environment():
    data=dict(policy=dict(version='quiz-test',timeout_seconds=120,stage_timeout_seconds=60,max_calls=1,max_input_tokens=1000,max_output_tokens=100),
        settings=dict(provider='openai',model='synthetic',prompt_version='remote-quiz-generation-v1',language='ko',budget_policy_version='quiz-test'),
        window_key='synthetic',review_seconds=60,web_origin='https://example.test')
    return dict(REMOTE_QUIZZES_ENABLED='true',REMOTE_QUIZ_DATABASE_URL='postgresql://quiz:synthetic@localhost/db',REMOTE_QUIZ_SETTINGS_JSON=json.dumps(data))


def test_disabled_ignores_credentials_and_repr_hides_secrets():
    assert load_quiz_settings({}) is None
    assert load_quiz_settings({'REMOTE_QUIZ_DATABASE_URL':'bad'}) is None
    assert 'synthetic' not in repr(load_quiz_settings(environment()))


@pytest.mark.parametrize('change',['flag','missing_dsn','fallback','duplicate','unknown','policy','prompt','origin','calls','ssl','query'])
def test_invalid_settings_fail_before_connection(change):
    env=environment();data=json.loads(env['REMOTE_QUIZ_SETTINGS_JSON'])
    if change=='flag':env['REMOTE_QUIZZES_ENABLED']='yes'
    if change in ('missing_dsn','fallback'):env.pop('REMOTE_QUIZ_DATABASE_URL')
    if change=='fallback':env['DATABASE_URL']='postgresql://main:secret@localhost/db'
    if change=='unknown':data['api_key']='secret'
    if change=='policy':data['settings']['budget_policy_version']='different'
    if change=='prompt':data['settings']['prompt_version']='other'
    if change=='origin':data['web_origin']='http://example.test'
    if change=='calls':data['policy']['max_calls']=2
    if change=='ssl':env['REMOTE_QUIZ_DATABASE_URL']='postgresql://quiz:secret@remote/db'
    if change=='query':env['REMOTE_QUIZ_DATABASE_URL']+='?options=anything'
    env['REMOTE_QUIZ_SETTINGS_JSON']=json.dumps(data)
    if change=='duplicate':env['REMOTE_QUIZ_SETTINGS_JSON']=env['REMOTE_QUIZ_SETTINGS_JSON'][:-1]+',"window_key":"other"}'
    with pytest.raises(ValueError,match='^quiz_settings_invalid$'):load_quiz_settings(env)


def configured(monkeypatch):
    for k,v in environment().items():monkeypatch.setenv(k,v)
    store=NS(connection_pool=object(),open=Mock(),close=Mock())
    factory=Mock(return_value=store);monkeypatch.setattr(PostgresDeviceStore,'from_dsn',factory)
    assemble=Mock(return_value=None);monkeypatch.setattr(remote_mcp,'create_remote_mcp',assemble)
    return store,factory,assemble


def test_app_rejects_disabled_mcp_and_double_injection(monkeypatch):
    _,factory,_=configured(monkeypatch)
    with pytest.raises(ValueError,match='quiz_settings_invalid'):create_app(device_store=NS())
    with pytest.raises(ValueError,match='quiz_settings_invalid'):
        create_app(device_store=NS(),remote_mcp_settings=NS(enabled=True,product_reads_enabled=True),remote_product_queries=NS(pool=NS()),remote_quizzes=NS())
    factory.assert_not_called()


def test_app_shares_dedicated_service_with_web_and_mcp_and_closes_pool(monkeypatch):
    from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
    from solo_leveling.interfaces.http.remote_quiz_generation_reviews import PostgresQuizGenerationReviews
    store,factory,assemble=configured(monkeypatch);captured=[]
    def view(self,owner,*args):captured.append((self.pool,owner));return {'status':'ok'}
    monkeypatch.setattr(PostgresQuizGenerationReviews,'view',view)
    app=create_app(device_store=NS(),remote_mcp_settings=NS(enabled=True,product_reads_enabled=True),remote_product_queries=NS(pool=NS()),
        token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice','web_session',None)))
    factory.assert_called_once_with(environment()['REMOTE_QUIZ_DATABASE_URL']);store.open.assert_not_called()
    assert assemble.call_args.kwargs['quiz_service'].pool is store.connection_pool
    assert assemble.call_args.kwargs['quiz_review_service'].pool is store.connection_pool
    with TestClient(app) as client:
        r=client.get('/v1/remote-quiz-generation/reviews/00000000-0000-0000-0000-000000000593',headers={'Authorization':'Bearer synthetic'})
        assert r.status_code==200 and captured==[(store.connection_pool,'alice')]
    store.open.assert_called_once();store.close.assert_called_once()


def test_failed_pool_open_is_closed(monkeypatch):
    store,_,_=configured(monkeypatch);store.open.side_effect=RuntimeError('synthetic')
    app=create_app(device_store=NS(),remote_mcp_settings=NS(enabled=True,product_reads_enabled=True),remote_product_queries=NS(pool=NS()))
    with pytest.raises(RuntimeError):
        with TestClient(app):pass
    store.close.assert_called_once()


def test_remote_pool_uses_trusted_ca_without_changing_other_connections(monkeypatch):
    import certifi
    from psycopg.conninfo import conninfo_to_dict
    store,factory,_=configured(monkeypatch)
    monkeypatch.setenv('REMOTE_QUIZ_DATABASE_URL','postgresql://quiz:synthetic@db.example.test/quiz?sslmode=verify-full')
    monkeypatch.setenv('PGSSLROOTCERT','/unchanged/other-ca.pem')
    monkeypatch.setattr(certifi,'where',lambda:'/trusted bundle/ca.pem')
    create_app(device_store=NS(),remote_mcp_settings=NS(enabled=True,product_reads_enabled=True),remote_product_queries=NS(pool=NS()))
    factory.assert_called_once()
    args=conninfo_to_dict(factory.call_args.args[0])
    assert args['sslmode']=='verify-full' and args['sslrootcert']=='/trusted bundle/ca.pem'
    assert args['host']=='db.example.test' and args['user']=='quiz' and args['dbname']=='quiz'
    import os
    assert os.environ['PGSSLROOTCERT']=='/unchanged/other-ca.pem'
    store.open.assert_not_called()


def test_review_injection_conflict_is_rejected_before_pool(monkeypatch):
    _,factory,_=configured(monkeypatch)
    with pytest.raises(ValueError,match='quiz_settings_invalid'):
        create_app(device_store=NS(),remote_mcp_settings=NS(enabled=True,product_reads_enabled=True),remote_product_queries=NS(pool=NS()),remote_quiz_reviews=NS())
    factory.assert_not_called()
