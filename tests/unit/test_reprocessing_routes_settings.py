"""복구 기능의 기본 차단·전용 설정·웹 인증과 CSRF 경계."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.remote_reprocessing import validate_contract
from solo_leveling.interfaces.http.remote_reprocessing_settings import load_reprocessing_settings
from tests.integration.test_remote_reprocessing_postgres import CONFIG


def environment():
    return dict(REMOTE_REPROCESSING_ENABLED='true',REMOTE_REPROCESSING_DATABASE_URL='postgresql://test:synthetic@localhost/db',
                REMOTE_REPROCESSING_SETTINGS_JSON=json.dumps({'config':CONFIG}))


def test_defaults_and_strict_dedicated_settings():
    assert load_reprocessing_settings({}) is None
    env=environment();assert 'synthetic' not in repr(load_reprocessing_settings(env))
    del env['REMOTE_REPROCESSING_DATABASE_URL'];env['DATABASE_URL']='postgresql://main:secret@localhost/db'
    with pytest.raises(ValueError):load_reprocessing_settings(env)


@pytest.mark.parametrize('field,value',[('max_calls',True),('max_input_tokens',0),('review_seconds',0),
    ('input_usd_per_million','NaN'),('input_usd_per_million','-1'),('output_usd_per_million',1),('model','')])
def test_invalid_cost_contract(field,value):
    with pytest.raises(ValueError):validate_contract(dict(CONFIG,**{field:value}))


def test_tls_and_unknown_settings():
    for dsn in ('postgresql://test:secret@external/db','postgresql://test:secret@localhost/db?options=unsafe'):
        with pytest.raises(ValueError):load_reprocessing_settings(dict(environment(),REMOTE_REPROCESSING_DATABASE_URL=dsn))
    env=environment();env['REMOTE_REPROCESSING_SETTINGS_JSON']='{"config":{},"config":{}}'
    with pytest.raises(ValueError):load_reprocessing_settings(env)


def test_web_only_no_store_and_untrusted_owner_rejected():
    key=str(uuid4());context=str(uuid4())
    for kind in ('device','web_session'):
        service=Mock();service.view.return_value={'state':'review'}
        app=create_app(device_store=NS(),device_registry=NS(is_active=lambda *_:True),remote_reprocessing=service,
            token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice',kind,'device' if kind=='device' else None)))
        with TestClient(app) as client:
            response=client.get('/v1/reprocessing/reviews/'+key,headers={'Authorization':'Bearer synthetic'})
            assert response.status_code==(200 if kind=='web_session' else 403)
            assert response.headers['cache-control']=='no-store'
            if kind=='web_session':
                service.view.assert_called_once_with('alice',key)
                response=client.post('/v1/reprocessing/reviews/'+key,headers={'Authorization':'Bearer synthetic'},
                    json={'context_id':context,'operation':'reparse','owner':'other'})
                assert response.status_code==422;service.prepare.assert_not_called()
            else:service.view.assert_not_called()


def test_cookie_csrf_blocks_admission():
    from tests.unit.test_http_web_auth import Store
    from solo_leveling.interfaces.http.web_auth import WebIdentity, SESSION_COOKIE, CSRF_COOKIE
    store=Store();store.sessions['session-token']=WebIdentity('alice','합성',None)
    service=Mock();service.submit.return_value={'state':'queued'}
    app=create_app(device_store=NS(),web_session_store=store,remote_reprocessing=service)
    with TestClient(app,base_url='https://example.test') as client:
        client.cookies.set(SESSION_COOKIE,'session-token');client.cookies.set(CSRF_COOKIE,'csrf-token')
        path='/v1/reprocessing/reviews/'+str(uuid4())+'/submit'
        assert client.post(path).status_code==403;service.submit.assert_not_called()
        assert client.post(path,headers={'X-CSRF-Token':'csrf-token'}).status_code==200


def test_environment_assembles_and_closes_dedicated_pool(monkeypatch):
    from solo_leveling.interfaces.http.devices import PostgresDeviceStore
    for k,v in environment().items():monkeypatch.setenv(k,v)
    store=NS(connection_pool=NS(),open=Mock(),close=Mock())
    factory=Mock(return_value=store);monkeypatch.setattr(PostgresDeviceStore,'from_dsn',factory)
    app=create_app(device_store=NS())
    factory.assert_called_once_with(environment()['REMOTE_REPROCESSING_DATABASE_URL'])
    with TestClient(app):store.open.assert_called_once()
    store.close.assert_called_once()
