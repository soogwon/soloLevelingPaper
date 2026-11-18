"""복구 요청의 인증·동일 범위·수신 확인 경계를 검증한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal, create_auth_dependency
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import (
    AnnotationRecoveryService, create_annotation_recovery_router,
)
from solo_leveling.interfaces.http.remote_annotations import (
    RemoteAnnotationRecoveryResult, RemoteAnnotationRecoveryAmbiguous, StoredRemoteAnnotation,
)
from solo_leveling.interfaces.http.web_auth import SESSION_COOKIE, CSRF_COOKIE

CONTEXT = '00000000-0000-0000-0000-000000000502'
ANNOTATION = '00000000-0000-0000-0000-000000000503'
BASE = f'/v1/remote-annotations/contexts/{CONTEXT}'


@pytest.fixture
def setup():
    store = Mock()
    result = RemoteAnnotationRecoveryResult('key', StoredRemoteAnnotation(
        ANNOTATION, CONTEXT, 'note', '현재 메모', None, (), 'user', 2, None, None),
        'committed', 'exact')
    store.lookup_request.return_value = result
    store.recover_request.return_value = result
    store.confirm_request.return_value = True
    verifier = Mock()
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'web_session')
    app = FastAPI()
    app.include_router(create_annotation_recovery_router(
        create_auth_dependency(verifier, verifier), AnnotationRecoveryService(store, 'product-web')))
    with TestClient(app) as client:
        yield client, store, verifier


def test_authentication_scope_and_current_revision(setup):
    client, store, verifier = setup
    assert client.get(BASE + '/requests/key').status_code == 401
    store.lookup_request.assert_not_called()
    response = client.get(BASE + '/requests/key', headers={'Authorization': 'Bearer synthetic'})
    assert response.status_code == 200
    assert response.json()['annotation']['revision'] == 2
    assert response.json()['reused'] is True
    assert response.headers['cache-control'] == 'no-store'
    store.lookup_request.assert_called_once_with('alice', 'product-web', CONTEXT, 'key')
    verifier.verify.return_value = AuthenticatedPrincipal('bob', 'web_session')
    store.lookup_request.return_value = None
    assert client.get(BASE + '/requests/key', headers={'Authorization': 'Bearer synthetic'}).status_code == 404
    store.lookup_request.assert_called_with('bob', 'product-web', CONTEXT, 'key')


def test_device_rejected_before_storage(setup):
    client, store, verifier = setup
    verifier.verify.return_value = AuthenticatedPrincipal('alice', 'device', 'device')
    assert client.get(BASE + '/requests/key', headers={'Authorization': 'Bearer synthetic'}).status_code == 403
    store.lookup_request.assert_not_called()


def test_cookie_csrf_and_initial_input_preserved(setup):
    client, store, _ = setup
    client.cookies.set(SESSION_COOKIE, 'session')
    client.cookies.set(CSRF_COOKIE, 'csrf')
    body = {'kind': 'note', 'body': ' 원래 메모\n', 'creation_origin': 'user'}
    assert client.post(BASE + '/recover', json=body).status_code == 403
    store.recover_request.assert_not_called()
    response = client.post(BASE + '/recover', json=body, headers={'X-CSRF-Token': 'csrf'})
    assert response.status_code == 200
    args = store.recover_request.call_args.args
    assert args[:3] == ('alice', 'product-web', CONTEXT)
    assert args[3].body == body['body']
    store.create.assert_not_called()


@pytest.mark.parametrize('extra', ['owner_user_id', 'service_id', 'request_key', 'session_id'])
def test_untrusted_scope_rejected(setup, extra):
    client, store, _ = setup
    response = client.post(BASE + '/recover', json={'kind': 'note', 'body': '메모', extra: 'forged'},
                           headers={'Authorization': 'Bearer synthetic'})
    assert response.status_code == 422
    store.recover_request.assert_not_called()


@pytest.mark.parametrize('error,status,code', [
    (RemoteAnnotationRecoveryAmbiguous(), 409, 'annotation_recovery_ambiguous'),
    (RuntimeError('비밀 본문'), 503, 'annotation_unavailable'),
    (ValueError('비밀 본문'), 422, 'annotation_input_invalid'),
])
def test_safe_errors(setup, error, status, code):
    client, store, _ = setup
    store.recover_request.side_effect = error
    response = client.post(BASE + '/recover', json={'kind': 'note', 'body': '메모'},
                           headers={'Authorization': 'Bearer synthetic'})
    assert response.status_code == status
    assert response.json() == {'detail': {'code': code}}
    assert response.headers['cache-control'] == 'no-store'


def test_confirm_checks_current_revision_and_never_creates(setup):
    client, store, _ = setup
    body = {'annotation_id': ANNOTATION, 'revision': 2}
    headers = {'Authorization': 'Bearer synthetic'}
    assert client.post(BASE + '/requests/key/confirm', json=body, headers=headers).status_code == 200
    store.confirm_request.assert_called_once_with('alice', 'product-web', CONTEXT, 'key', ANNOTATION, 2)
    store.confirm_request.return_value = False
    assert client.post(BASE + '/requests/key/confirm', json=body, headers=headers).status_code == 409
    body['revision'] = True
    assert client.post(BASE + '/requests/key/confirm', json=body, headers=headers).status_code == 422
    store.create.assert_not_called()


def test_default_app_has_no_recovery_routes():
    from solo_leveling.interfaces.http.app import create_app
    app = create_app(device_store=NS())
    assert not any(getattr(route, 'path', '').startswith('/v1/remote-annotations/') for route in app.routes)


def test_validation_error_does_not_echo_body(setup):
    client, store, _ = setup
    response = client.post(BASE + '/recover', json={'kind': 'invalid', 'body': '비밀 본문'},
                           headers={'Authorization': 'Bearer synthetic'})
    assert response.status_code == 422
    assert response.json() == {'detail': {'code': 'annotation_input_invalid'}}
    assert '비밀' not in response.text
    store.recover_request.assert_not_called()
