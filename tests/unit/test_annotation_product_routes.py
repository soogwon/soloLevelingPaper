"""동의 변경은 웹 세션·CSRF와 명시적 고지를 요구한다."""
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal, create_auth_dependency
from solo_leveling.interfaces.http.remote_annotation_product_routes import create_annotation_product_router
from solo_leveling.interfaces.http.web_auth import SESSION_COOKIE, CSRF_COOKIE


def test_csrf_device_and_extra_fields_cannot_grant():
    service = Mock(); verifier = Mock()
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'web_session')
    service.consents.grant.return_value = {'granted': True}
    app = FastAPI()
    app.include_router(create_annotation_product_router(create_auth_dependency(verifier, verifier), service))
    path = '/v1/remote-annotation-products/contexts/00000000-0000-0000-0000-000000000580/consent'
    body = {'notice_version': 'remote-annotation-storage-v1'}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        client.cookies.set(SESSION_COOKIE, 'session'); client.cookies.set(CSRF_COOKIE, 'csrf')
        assert client.post(path, json=body).status_code == 403
        service.consents.grant.assert_not_called()
        assert client.post(path, json={**body, 'confirmed': True}, headers={'X-CSRF-Token': 'csrf'}).status_code == 422
        service.consents.grant.assert_not_called()
        assert client.post(path, json=body, headers={'X-CSRF-Token': 'csrf'}).status_code == 200
        service.consents.grant.assert_called_once()
        verifier.verify.return_value = AuthenticatedPrincipal('alice', 'device', 'device')
        assert client.post(path, json=body, headers={'Authorization': 'Bearer synthetic'}).status_code == 403
        assert service.consents.grant.call_count == 1


def test_context_listing_uses_authenticated_owner_and_fixed_version():
    service = Mock(); verifier = Mock()
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'web_session')
    service.learning.list_for_paper.return_value = {'items': [], 'next_cursor': None}
    app = FastAPI()
    app.include_router(create_annotation_product_router(create_auth_dependency(verifier, verifier), service))
    paper = '00000000-0000-0000-0000-000000000580'
    version = '00000000-0000-0000-0000-000000000581'
    path = f'/v1/remote-annotation-products/contexts?paper_id={paper}&version_id={version}&owner=other'
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        client.cookies.set(SESSION_COOKIE, 'session')
        result = client.get(path)
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'no-store'
        service.learning.list_for_paper.assert_called_once_with('alice', paper, version, after=None, limit=20)
        assert client.get(path.replace(version, '')).status_code == 422
