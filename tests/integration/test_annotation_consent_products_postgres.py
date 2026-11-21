"""맥락별 저장 동의와 실제 HTTP CRUD를 합성 계정·격리 DB로 검증한다."""
# ruff: noqa: F811
import os
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_annotations_postgres import context, annotation  # noqa: F401
from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.remote_annotation_products import RemoteAnnotationProducts
from solo_leveling.interfaces.http.remote_annotation_consents import NOTICE_VERSION, AnnotationStorageConsentRequired

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_context_consent_replay_revoke_and_history(pool, db, context):
    owner, key, _ = context
    service = RemoteAnnotationProducts(pool, 'product')
    assert service.consents.view(owner, key) is None
    service.learning.start(owner, key)
    assert not service.consents.view(owner, key)['granted']
    value = annotation(context)
    with pytest.raises(AnnotationStorageConsentRequired):
        service.create(owner, key, 'before-consent', value)
    first = service.consents.grant(owner, key, NOTICE_VERSION)
    assert service.consents.grant(owner, key, NOTICE_VERSION) == first
    saved = service.create(owner, key, 'key', value)
    service.consents.revoke(owner, key, first['consent_id'])
    assert service.create(owner, key, 'key', value).reused
    with pytest.raises(AnnotationStorageConsentRequired):
        service.create(owner, key, 'new', value)
    with pytest.raises(AnnotationStorageConsentRequired):
        service.update(owner, key, saved.annotation.annotation_id, '새 내용', None, 1)
    assert service.store.get_annotation(owner, key, saved.annotation.annotation_id) is not None
    assert service.store.delete_annotation(owner, key, saved.annotation.annotation_id, expected_revision=1)
    second = service.consents.grant(owner, key, NOTICE_VERSION)
    assert second['revision'] == 2 and second['consent_id'] != first['consent_id']
    with pytest.raises(ValueError):
        service.consents.revoke(owner, key, first['consent_id'])
    assert service.consents.view(owner, key)['granted']
    assert db('SELECT count(*) n FROM server_annotation_consents WHERE owner_user_id=%s', (owner,))[0]['n'] == 2


def test_http_learning_consent_crud_and_scope(pool, context):
    owner, key, _ = context
    service = RemoteAnnotationProducts(pool, 'product')
    principal = NS(value=AuthenticatedPrincipal(owner, 'web_session'))
    app = create_app(device_store=NS(), token_verifier=NS(verify=lambda _: principal.value),
                     remote_annotation_products=service)
    base = f'/v1/remote-annotation-products/contexts/{key}'
    headers = {'Authorization': 'Bearer synthetic'}
    with TestClient(app) as client:
        assert client.get(base + '/learning', headers=headers).status_code == 404
        assert client.post(base + '/start', headers=headers).status_code == 200
        assert client.post(base + '/requests/key', json=asdict(annotation(context)), headers=headers).status_code == 403
        consent = client.post(base + '/consent', json={'notice_version': NOTICE_VERSION}, headers=headers)
        assert consent.status_code == 200
        response = client.post(base + '/requests/key', json=asdict(annotation(context)), headers=headers)
        assert response.status_code == 200
        item = response.json()['annotation']
        path = base + '/annotations/' + item['annotation_id']
        assert client.patch(path, json={'body': '수정', 'revision': 1}, headers=headers).status_code == 200
        assert client.patch(path, json={'body': '오래됨', 'revision': 1}, headers=headers).status_code == 409
        assert len(client.get(base + '/annotations', headers=headers).json()['items']) == 1
        assert client.post(base + '/consent/revoke', json={'consent_id': consent.json()['consent_id']}, headers=headers).status_code == 200
        assert client.patch(path, json={'body': '철회 후', 'revision': 2}, headers=headers).status_code == 403
        principal.value = AuthenticatedPrincipal('other', 'web_session')
        assert client.get(path, headers=headers).status_code == 404
        principal.value = AuthenticatedPrincipal(owner, 'web_session')
        assert client.request('DELETE', path, json={'revision': 2}, headers=headers).status_code == 200


def test_consent_never_carries_to_another_context(pool, db, context):
    from uuid import uuid4
    owner, key, revisions = context
    other = str(uuid4())
    db('''INSERT INTO server_execution_contexts
        (owner_user_id,context_id,paper_id,server_version_id,publication_id,parse_revision_id)
        SELECT owner_user_id,%s,paper_id,server_version_id,publication_id,parse_revision_id
        FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s''', (other, owner, key))
    service = RemoteAnnotationProducts(pool, 'product')
    service.learning.start(owner, key)
    service.learning.start(owner, other)
    service.consents.grant(owner, key, NOTICE_VERSION)
    assert not service.consents.view(owner, other)['granted']
    with pytest.raises(AnnotationStorageConsentRequired):
        service.create(owner, other, 'separate', annotation((owner, other, revisions)))
    assert service.consents.grant('other-owner', key, NOTICE_VERSION) is None
    assert db('SELECT count(*) n FROM server_annotation_consents WHERE context_id=%s', (other,))[0]['n'] == 0


def test_account_storage_consent_allows_saving_until_context_revocation(pool, db, context):
    from solo_leveling.interfaces.http.account_consents import (
        NOTICE_VERSION as ACCOUNT_NOTICE, PostgresAccountConsents)
    owner, key, _ = context
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성') ON CONFLICT DO NOTHING", (owner,))
    service = RemoteAnnotationProducts(pool, 'product')
    service.learning.start(owner, key)
    value = annotation(context)
    with pytest.raises(AnnotationStorageConsentRequired):
        service.create(owner, key, 'account-before', value)
    PostgresAccountConsents(pool).grant(owner, ['storage'], ACCOUNT_NOTICE)
    view = service.consents.view(owner, key)
    assert view['granted'] and view['account'] and view['consent_id'] is None
    saved = service.create(owner, key, 'account-key', value)
    assert saved.annotation.body == value.body

    # 이 맥락에서 따로 동의한 뒤 철회하면 계정 동의가 있어도 새 저장을 막는다.
    own = service.consents.grant(owner, key, NOTICE_VERSION)
    assert own['granted'] and not own['account']
    revoked = service.consents.revoke(owner, key, own['consent_id'])
    assert not revoked['granted'] and not revoked['account']
    with pytest.raises(AnnotationStorageConsentRequired):
        service.create(owner, key, 'account-after-revoke', value)

    PostgresAccountConsents(pool).revoke(owner, 'storage')
    assert not service.consents.view(owner, key)['account']
