"""인증 HTTP 경계부터 실제 저장소까지 복구·확정·삭제를 검증한다."""
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
from solo_leveling.interfaces.http.remote_annotations import PostgresRemoteAnnotationStore
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import AnnotationRecoveryService

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_recovery_confirm_and_deleted_payload(pool, db, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)
    saved = store.create(owner, 'product-web', context_id, 'lost', value)
    principal = NS(value=AuthenticatedPrincipal(owner, 'web_session'))
    app = create_app(device_store=NS(), token_verifier=NS(verify=lambda _: principal.value),
                     remote_annotation_recovery=AnnotationRecoveryService(store, 'product-web'))
    base = f'/v1/remote-annotations/contexts/{context_id}'
    headers = {'Authorization': 'Bearer synthetic'}
    with TestClient(app) as client:
        response = client.post(base + '/recover', json=asdict(value), headers=headers)
        assert response.status_code == 200
        row = response.json()
        assert row['request_key'] == 'lost' and row['recovery'] == 'unique_unconfirmed'
        assert row['annotation']['annotation_id'] == saved.annotation.annotation_id
        confirmation = {'annotation_id': row['annotation']['annotation_id'], 'revision': 1}
        for _ in range(2):
            assert client.post(base + '/requests/lost/confirm', json=confirmation, headers=headers).status_code == 200
        assert client.post(base + '/recover', json=asdict(value), headers=headers).status_code == 404
        assert client.get(base + '/requests/lost', headers=headers).status_code == 200
        principal.value = AuthenticatedPrincipal('00000000-0000-0000-0000-000000000000', 'web_session')
        assert client.get(base + '/requests/lost', headers=headers).status_code == 404
        principal.value = AuthenticatedPrincipal(owner, 'web_session')
        assert store.mark_deleted(owner, saved.annotation.annotation_id)
        row = client.get(base + '/requests/lost', headers=headers).json()
        assert row['state'] == 'deleted'
        assert row['annotation']['body'] == '' and row['annotation']['segments'] == []
        assert client.post(base + '/requests/lost/confirm', json=confirmation, headers=headers).status_code == 409
    assert db('SELECT count(*) n FROM server_context_annotations WHERE owner_user_id=%s', (owner,))[0]['n'] == 1
