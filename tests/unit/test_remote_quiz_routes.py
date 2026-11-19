"""퀴즈 저장 동의는 인증 웹 세션에서만 저장한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal

QUIZ='00000000-0000-0000-0000-000000000593'
CONTEXT='00000000-0000-0000-0000-000000000592'


def test_web_only_owner_and_no_model_confirmation():
    for kind in ('device','web_session'):
        service=Mock()
        service.consent.return_value={'storage':'missing'}
        app=create_app(token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice',kind,'device' if kind=='device' else None)),
            device_store=NS(),device_registry=NS(is_active=lambda *_:True),remote_quizzes=service)
        with TestClient(app) as client:
            response=client.get(f'/v1/remote-quizzes/{CONTEXT}/{QUIZ}/consent',headers={'Authorization':'Bearer synthetic'})
            assert response.status_code==(200 if kind=='web_session' else 403)
            if kind=='web_session':service.consent.assert_called_once_with('alice',CONTEXT,QUIZ)
            else:service.consent.assert_not_called()
            response=client.post(f'/v1/remote-quizzes/{CONTEXT}/{QUIZ}/consent',headers={'Authorization':'Bearer synthetic'},
                json={'notice_version':'remote-quiz-attempt-v1','quiz_revision':'a'*64,'confirmed':True})
            assert response.status_code in (403,422)
            service.grant_storage.assert_not_called()


def test_explicit_grant_passes_verified_owner_and_fixed_quiz_only():
    service=Mock()
    service.grant_storage.return_value={'storage':'current'}
    app=create_app(token_verifier=NS(verify=lambda _:AuthenticatedPrincipal('alice','web_session',None)),
        device_store=NS(),remote_quizzes=service)
    with TestClient(app) as client:
        result=client.post(f'/v1/remote-quizzes/{CONTEXT}/{QUIZ}/consent',headers={'Authorization':'Bearer synthetic'},
            json={'notice_version':'remote-quiz-attempt-v1','quiz_revision':'a'*64})
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
        service.grant_storage.assert_called_once_with('alice',CONTEXT,QUIZ,'remote-quiz-attempt-v1','a'*64)
        service.consent.side_effect=RuntimeError('비밀 설정')
        result=client.get(f'/v1/remote-quizzes/{CONTEXT}/{QUIZ}/consent',headers={'Authorization':'Bearer synthetic'})
        assert result.status_code==503 and '비밀' not in result.text
