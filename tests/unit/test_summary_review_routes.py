from types import SimpleNamespace as NS
from unittest.mock import Mock
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal

KEY='00000000-0000-0000-0000-000000000399'


def client_for(kind):
    service=Mock();service.view.return_value={'status':'ok'};service.grant.return_value={'status':'ok'}
    app=create_app(token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice',kind,'device' if kind=='device' else None)),
        device_store=NS(),device_registry=NS(is_active=lambda *_:True),remote_summaries=service)
    return TestClient(app),service


def test_web_only_and_fixed_owner():
    for kind in ('device','web_session'):
        client,service=client_for(kind)
        with client:
            response=client.get(f'/v1/remote-summaries/reviews/{KEY}',headers={'Authorization':'Bearer synthetic'})
            assert response.status_code==(200 if kind=='web_session' else 403)
            if kind=='web_session':service.view.assert_called_once_with('alice',KEY)
            else:service.view.assert_not_called()


def test_model_confirmed_field_is_not_consent():
    client,service=client_for('web_session')
    with client:
        response=client.post(f'/v1/remote-summaries/reviews/{KEY}/consents/ai_provider',headers={'Authorization':'Bearer synthetic'},json={'notice_version':'remote-summary-v1','confirmed':True})
        assert response.status_code==422
        service.grant.assert_not_called()
