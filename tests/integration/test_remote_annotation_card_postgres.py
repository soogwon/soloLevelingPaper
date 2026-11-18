"""실제 격리 DB에서 카드 조회·수신 확정과 늦은 응답을 검증한다."""
# ruff: noqa: F811
import os
import time
from types import SimpleNamespace

import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_annotations_postgres import context, annotation  # noqa: F401
from tests.unit.test_remote_annotation_tools import Server
from solo_leveling.interfaces.mcp import remote_annotations as module
from solo_leveling.interfaces.http.remote_annotations import PostgresRemoteAnnotationStore
from solo_leveling.interfaces.http.remote_annotation_recovery_routes import AnnotationRecoveryService
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_saved_response_loss_card_confirmation_is_idempotent(monkeypatch, pool, db, context):
    owner, ctx, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)
    saved = store.create(owner, 'remote-paper', ctx, 'lost-card', value)
    token = RemoteIdentityAccessToken(token='synthetic-card', client_id='test',
        scopes=['annotations:read', 'annotations:write'], expires_at=int(time.time()) + 60)
    token._verified_identity = VerifiedRemoteIdentity('https://issuer.example/', 'alice')
    monkeypatch.setattr(module, 'get_access_token', lambda: token)
    server = Server()
    module.register_annotation_tools(server, SimpleNamespace(owner=lambda: owner),
                                    AnnotationRecoveryService(store, 'remote-paper'))
    show = server.tools['show_remote_annotation_recovery']
    confirm = server.tools['confirm_remote_annotation_card']
    result = show(ctx, 'lost-card')['result']
    assert result['recovery']['annotation']['annotation_id'] == saved.annotation.annotation_id
    assert store.recover_request(owner, 'remote-paper', ctx, value) is not None
    bound = result['binding']
    # 카드 표시 뒤 다른 기기에서 revision을 갱신한 경합을 재현한다.
    db("UPDATE server_context_annotations SET revision=2 WHERE annotation_id=%s", (saved.annotation.annotation_id,))
    assert confirm(ctx, 'lost-card', saved.annotation.annotation_id, 1, bound)['result']['confirmed'] is False
    current = show(ctx, 'lost-card', bound)['result']['recovery']['annotation']
    assert current['revision'] == 2
    args = (ctx, 'lost-card', current['annotation_id'], 2, bound)
    assert confirm(*args)['result']['confirmed'] is True
    before = db('SELECT client_confirmed_at FROM server_annotation_create_requests WHERE annotation_id=%s', (current['annotation_id'],))
    assert confirm(*args)['result']['confirmed'] is True
    assert before == db('SELECT client_confirmed_at FROM server_annotation_create_requests WHERE annotation_id=%s', (current['annotation_id'],))
    assert db('SELECT count(*) n FROM server_context_annotations WHERE owner_user_id=%s', (owner,))[0]['n'] == 1
    token.token = 'changed-card-session'
    assert confirm(*args)['result']['connection_changed'] is True
