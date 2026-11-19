from types import SimpleNamespace as NS
from unittest.mock import Mock
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal

KEY='00000000-0000-0000-0000-000000000399'


def client_for(kind):
    service=Mock();service.view.return_value={'status':'ok'};service.grant.return_value={'status':'ok'}
    app=create_app(token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice',kind,'device' if kind=='device' else None)),
        device_store=NS(),device_registry=NS(is_active=lambda *_:True),remote_quiz_reviews=service)
    return TestClient(app),service


def test_web_only_and_fixed_owner():
    for kind in ('device','web_session'):
        client,service=client_for(kind)
        with client:
            response=client.get(f'/v1/remote-quiz-generation/reviews/{KEY}',headers={'Authorization':'Bearer synthetic'})
            assert response.status_code==(200 if kind=='web_session' else 403)
            if kind=='web_session':service.view.assert_called_once_with('alice',KEY)
            else:service.view.assert_not_called()


def test_model_confirmed_field_is_not_consent():
    client,service=client_for('web_session')
    with client:
        response=client.post(f'/v1/remote-quiz-generation/reviews/{KEY}/consents/ai_provider',headers={'Authorization':'Bearer synthetic'},json={'notice_version':'remote-quiz-generation-v1','input_hash':'remote-quiz-generation-v1:hash','confirmed':True})
        assert response.status_code==422
        service.grant.assert_not_called()


def test_cookie_mutations_require_csrf():
    from tests.unit.test_http_web_auth import Store,NOW
    from solo_leveling.interfaces.http.web_auth import WebIdentity,SESSION_COOKIE,CSRF_COOKIE
    store=Store();store.create_session(WebIdentity('alice','합성',None),NOW)
    service=Mock();service.submit.return_value={'state':'queued'}
    app=create_app(web_session_store=store,remote_quiz_reviews=service)
    with TestClient(app,base_url='https://example.test') as client:
        client.cookies.set(SESSION_COOKIE,'session-token');client.cookies.set(CSRF_COOKIE,'csrf-token')
        path=f'/v1/remote-quiz-generation/reviews/{KEY}/submit'
        assert client.post(path).status_code==403
        service.submit.assert_not_called()
        assert client.post(path,headers={'X-CSRF-Token':'csrf-token'}).status_code==200
        service.submit.assert_called_once_with('alice',KEY)


def test_errors_are_safe_and_service_is_opt_in():
    client,service=client_for('web_session')
    with client:
        for error,code in [(ValueError('합성 비밀'),409),(RuntimeError('합성 비밀'),503)]:
            service.view.side_effect=error
            result=client.get(f'/v1/remote-quiz-generation/reviews/{KEY}',headers={'Authorization':'Bearer synthetic'})
            assert result.status_code==code and '비밀' not in result.text
        service.view.side_effect=None;service.view.return_value=None
        assert client.get(f'/v1/remote-quiz-generation/reviews/{KEY}',headers={'Authorization':'Bearer synthetic'}).status_code==404
    app=create_app(token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice','web_session')))
    with TestClient(app) as client:
        assert client.get(f'/v1/remote-quiz-generation/reviews/{KEY}',headers={'Authorization':'Bearer synthetic'}).status_code==503
