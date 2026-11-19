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


def test_annotation_listing_filters_kind_with_authenticated_owner():
    service = Mock(); verifier = Mock()
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'web_session')
    service.store.list_annotations.return_value = {
        'items': [], 'next_cursor': None, 'total_count': 0}
    app = FastAPI()
    app.include_router(create_annotation_product_router(create_auth_dependency(verifier, verifier), service))
    context = '00000000-0000-0000-0000-000000000580'
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, 'session')
        result = client.get(f'/v1/remote-annotation-products/contexts/{context}/annotations?kind=note&limit=3')
        assert result.status_code == 200
        service.store.list_annotations.assert_called_once_with(
            'alice', context, after=None, limit=3, kind='note', include_total=True)
        assert client.get(f'/v1/remote-annotation-products/contexts/{context}/annotations?kind=other').status_code == 422


def test_progress_requires_session_csrf_strict_revision_and_maps_conflict():
    from solo_leveling.interfaces.http.remote_learning import RemoteLearningConflict
    service = Mock(); verifier = Mock()
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'web_session')
    service.learning.set_progress.return_value = {'state': {'status': 'completed'}, 'replayed': False}
    app = FastAPI()
    app.include_router(create_annotation_product_router(create_auth_dependency(verifier, verifier), service))
    context = '00000000-0000-0000-0000-000000000580'
    path = f'/v1/remote-annotation-products/contexts/{context}/progress'
    body = {'request_key': context, 'status': 'completed', 'pdf_page': 1, 'expected_revision': 1}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        client.cookies.set(SESSION_COOKIE, 'session'); client.cookies.set(CSRF_COOKIE, 'csrf')
        assert client.post(path, json=body).status_code == 403
        headers = {'X-CSRF-Token': 'csrf'}
        for changes in ({'expected_revision': True}, {'pdf_page': True}, {'owner': 'other'}, {'status': 'published'}):
            assert client.post(path, json={**body, **changes}, headers=headers).status_code == 422
        service.learning.set_progress.assert_not_called()
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        service.learning.set_progress.assert_called_once_with('alice', context, **body)
        service.learning.set_progress.side_effect = RemoteLearningConflict('private-detail')
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 409 and 'private-detail' not in result.text
        verifier.verify.return_value = AuthenticatedPrincipal('alice', 'device', 'device')
        assert client.post(path, json=body, headers={'Authorization': 'Bearer synthetic'}).status_code == 403


def test_dashboard_is_read_only_session_scoped_and_bounded():
    service = Mock(); verifier = Mock()
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'web_session')
    service.learning.dashboard.return_value = {'items': [], 'next_cursor': None}
    app = FastAPI()
    app.include_router(create_annotation_product_router(create_auth_dependency(verifier, verifier), service))
    with TestClient(app) as client:
        path = '/v1/remote-annotation-products/contexts/dashboard'
        assert client.get(path).status_code == 401
        client.cookies.set(SESSION_COOKIE, 'session')
        result = client.get(path + '?owner=other')
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        service.learning.dashboard.assert_called_once_with('alice', after=None, limit=20)
        assert client.get(path + '?limit=51').status_code == 422
        service.learning.start.assert_not_called()
