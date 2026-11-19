"""성찰 동의는 인증 웹 세션에서만 저장한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal

CONTEXT='00000000-0000-0000-0000-000000000592'


def test_web_only_owner_and_no_model_confirmation():
    for kind in ('device','web_session'):
        service=Mock()
        service.consent.return_value={'storage':'missing'}
        app=create_app(token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice',kind,'device' if kind=='device' else None)),
            device_store=NS(),device_registry=NS(is_active=lambda *_:True),remote_reflections=service)
        with TestClient(app) as client:
            response=client.get(f'/v1/remote-reflections/{CONTEXT}/consent',headers={'Authorization':'Bearer synthetic'})
            assert response.status_code==(200 if kind=='web_session' else 403)
            if kind=='web_session':service.consent.assert_called_once_with('alice',CONTEXT)
            else:service.consent.assert_not_called()
            response=client.post(f'/v1/remote-reflections/{CONTEXT}/consent',headers={'Authorization':'Bearer synthetic'},
                json={'notice_version':'remote-reflection-v1','question_revision':'a'*64,'confirmed':True})
            assert response.status_code in (403,422)
            service.grant_storage.assert_not_called()
