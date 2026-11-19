"""웹 세션으로만 저장 요약을 읽으며 기존 검토 URL을 유지한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.remote_summary_routes import create_summary_review_router

ID='00000000-0000-0000-0000-000000000581'


def client(kind='web_session', service=None):
    app=FastAPI();service=service or Mock()
    app.include_router(create_summary_review_router(lambda:NS(user_id='alice',credential_type=kind),service))
    return TestClient(app),service


def test_get_uses_session_owner_and_preserves_review_url():
    service=Mock();service.contexts.return_value={'items':[],'next_cursor':None};service.latest.return_value={'state':'completed'};service.view.return_value={'state':'prepared'}
    c,_=client(service=service)
    r=c.get(f'/v1/remote-summaries/papers/{ID}/contexts',params={'version_id':'v1'})
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    service.contexts.assert_called_once_with('alice',ID,'v1',None)
    assert c.get(f'/v1/remote-summaries/contexts/{ID}/latest').status_code==200
    service.latest.assert_called_once_with('alice',ID)
    assert c.get(f'/v1/remote-summaries/reviews/{ID}').status_code==200
    service.submit.assert_not_called()


def test_non_web_identity_and_missing_or_failed_result():
    c,s=client('bearer');assert c.get(f'/v1/remote-summaries/contexts/{ID}/latest').status_code==403;s.latest.assert_not_called()
    c,s=client();s.latest.return_value=None;assert c.get(f'/v1/remote-summaries/contexts/{ID}/latest').status_code==404
    s.latest.side_effect=RuntimeError('비밀');r=c.get(f'/v1/remote-summaries/contexts/{ID}/latest');assert r.status_code==503 and '비밀' not in r.text
    assert r.headers['cache-control']=='no-store'
