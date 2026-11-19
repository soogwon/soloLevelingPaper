"""기존 파일과 고정 청크의 이관을 제한 역할 PostgreSQL에서 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import sql

from solo_leveling.interfaces.http.remote_insights import InsightConflict, InsightImport, PostgresRemoteInsightImports, digest
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries
from tests.integration.test_processing_publication_postgres import prepared, publish
from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


@pytest.fixture
def fixture(pool, db, request):
    publisher, source, fingerprint = prepared(pool, db)
    publication = publish(publisher, source, fingerprint)
    context, device = uuid4(), uuid4()
    owner = source.owner_user_id
    db('INSERT INTO server_devices(device_id,owner_user_id,display_name) VALUES (%s,%s,%s)',
       (device, owner, '테스트 기기'))
    db('''INSERT INTO server_execution_contexts
        (owner_user_id,context_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)''',
       (owner, context, source.paper_id, source.version_id, publication.publication_id, source.parse_revision_id))
    if getattr(request, 'param', 'local') == 'local':
        db("""UPDATE server_pdf_uploads SET local_version_id='local-version',source_kind='local'
            WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s""",
           (owner, source.paper_id, source.version_id))
    binding = db('''SELECT c.chunk_id,c.original_text,c.pdf_page,c.section_id,t.translated_text
        FROM server_processing_chunks c JOIN server_translation_chunks t USING(owner_user_id,chunk_id)
        WHERE c.parse_revision_id=%s''', (source.parse_revision_id,))[0]
    original = binding['original_text']
    item = dict(insight_id='local-1', kind='keyword', text=original[:3],
                content_kind='extracted', producer='extractor', source_chunk_id='local-chunk',
                remote_chunk_id=binding['chunk_id'], original_sha256=digest(original),
                pdf_page=binding['pdf_page'], start_offset=0, end_offset=3, quote_original=original[:3])
    generated = dict(item, insight_id='local-2', kind='important_sentence', text='기존 저장 문장',
                     content_kind='generated', producer='model', source_translation_revision_id='local-translation',
                     translated_sha256=digest(binding['translated_text']))
    request = InsightImport.model_validate(dict(confirmed=True, request_key=uuid4(), source_collection_id='source-1',
        local_context_id='local-context', local_version_id='local-version', local_parse_revision_id='local-parse',
        pdf_sha256='a'*64, publication_id=publication.publication_id, parse_revision_id=source.parse_revision_id,
        translation_revision_id=source.translation_revision_id, embedding_set_id=source.embedding_set_id,
        section_id=binding['section_id'], stored_at=datetime.now(timezone.utc), items=[item, generated]))
    return SimpleNamespace(owner=owner, context=context, device=device, request=request)


def test_import_replay_conflict_replacement_and_detail(pool, db, fixture):
    x = fixture
    store = PostgresRemoteInsightImports(pool)
    draft = x.request.model_copy(update={'confirmed': False})
    preview = store.save(x.owner, x.device, x.context, draft, preview=True)
    assert preview['next_revision'] == 1 and len(preview['items']) == 2
    assert db('SELECT revision FROM server_remote_insight_collections WHERE context_id=%s', (x.context,)) == []
    with pytest.raises(InsightConflict, match='import_invalid'):
        store.save(x.owner, x.device, x.context, draft)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.save(x.owner,x.device,x.context,x.request), range(2)))
    assert results == [dict(source_collection_id='source-1', revision=1)]*2
    assert store.save('other', x.device, x.context, x.request) is None
    with pytest.raises(InsightConflict, match='request_conflict'):
        store.save(x.owner,x.device,x.context,x.request.model_copy(update={'expected_revision': 1}))
    with pytest.raises(InsightConflict, match='revision_conflict'):
        store.save(x.owner,x.device,x.context,x.request.model_copy(update={'request_key': uuid4()}))
    changed = x.request.model_copy(update={'request_key': uuid4(), 'expected_revision': 1})
    assert store.save(x.owner,x.device,x.context,changed)['revision'] == 2
    assert store.save(x.owner,x.device,x.context,x.request)['revision'] == 1

    queries = RemoteProductQueries(pool)
    queries.insights_enabled = True
    queries.owner = lambda: x.owner
    detail = queries.detail(str(x.context))['result']
    assert detail['insights_status'] == 'ready' and detail['section_catalog']
    assert detail['keywords'][0]['translation_revision_id'] is None
    assert detail['important_sentences'][0]['translation_revision_id'] == str(x.request.translation_revision_id)
    assert 'source_chunk_id' not in detail['keywords'][0]
    queries.owner = lambda: 'another-owner'
    assert queries.detail(str(x.context)) == {'status': 'not_found'}
    db('DELETE FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s',(x.owner,x.context))
    assert db('SELECT revision FROM server_remote_insight_collections WHERE context_id=%s',(x.context,)) == []
    assert db('SELECT applied_revision FROM server_remote_insight_requests WHERE context_id=%s',(x.context,)) == []


def test_source_and_permissions_fail_without_writes(pool, db, fixture):
    x = fixture
    store = PostgresRemoteInsightImports(pool)
    wrong = x.request.model_copy(update={'pdf_sha256':'b'*64})
    assert store.save(x.owner,x.device,x.context,wrong) is None
    with pytest.raises(InsightConflict, match='binding_changed'):
        store.save(x.owner,x.device,x.context,x.request.model_copy(update={'publication_id': uuid4()}))
    bad = x.request.items[1].model_copy(update={'translated_sha256':'b'*64})
    with pytest.raises(InsightConflict, match='source_mismatch'):
        store.save(x.owner,x.device,x.context,x.request.model_copy(update={'items':[x.request.items[0],bad]}))
    assert db('SELECT revision FROM server_remote_insight_collections WHERE context_id=%s',(x.context,)) == []
    import psycopg

    role = sql.Identifier('insight_test_'+uuid4().hex[:12])
    with pool.connection() as conn, conn.transaction():
        schema = sql.Identifier(conn.execute('SELECT current_schema() AS name').fetchone()['name'])
        conn.execute(sql.SQL('CREATE ROLE {}').format(role))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(schema,role))
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(role))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute('SELECT * FROM server_lock_insight_import(%s,%s,%s,%s,%s)',
                         (x.owner,x.device,x.context,x.request.local_version_id,x.request.pdf_sha256))
        conn.execute('RESET ROLE')
        conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION {}.server_lock_insight_import(text,uuid,uuid,text,text) TO {}').format(schema,role))
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(role))
        assert conn.execute('SELECT * FROM server_lock_insight_import(%s,%s,%s,%s,%s)',
                            (x.owner,x.device,x.context,x.request.local_version_id,x.request.pdf_sha256)).fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute('UPDATE server_devices SET revoked_at=now() WHERE device_id=%s',(x.device,))
        conn.execute('RESET ROLE')
        conn.execute(sql.SQL('DROP OWNED BY {}').format(role))
        conn.execute(sql.SQL('DROP ROLE {}').format(role))


@pytest.mark.parametrize('fixture', ['local', 'remote'], indirect=True)
def test_device_source_mapping_is_read_only_and_checks_owner_and_exact_hash(pool, db, fixture):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
    from solo_leveling.interfaces.http.remote_insight_routes import create_insight_import_router

    x = fixture
    principal = AuthenticatedPrincipal(x.owner, 'device', str(x.device))
    app = FastAPI()
    app.include_router(create_insight_import_router(lambda: principal, PostgresRemoteInsightImports(pool)))
    path = f'/v1/remote-insights/contexts/{x.context}/imports/map-sources'
    body = dict(local_version_id=x.request.local_version_id, pdf_sha256=x.request.pdf_sha256,
                section_scoped=True, sources=[source.model_dump(include={
                    'original_sha256', 'translated_sha256', 'pdf_page'}) for source in x.request.items])
    with TestClient(app) as client:
        response = client.post(path, json=body)
        assert response.status_code == 200
        assert response.json()['remote_chunk_ids'] == [str(item.remote_chunk_id) for item in x.request.items]
        assert response.json()['section_id'] == x.request.section_id
        assert response.json()['publication_id'] == str(x.request.publication_id)
        assert response.headers['cache-control'] == 'no-store'
        wrong = dict(body, sources=[dict(body['sources'][1], translated_sha256='b' * 64)])
        assert client.post(path, json=wrong).status_code == 409
        principal = AuthenticatedPrincipal('another-owner', 'device', str(x.device))
        assert client.post(path, json=body).status_code == 404
        principal = AuthenticatedPrincipal(x.owner, 'web_session')
        assert client.post(path, json=body).status_code == 403
    assert db('SELECT revision FROM server_remote_insight_collections WHERE context_id=%s', (x.context,)) == []



@pytest.mark.parametrize('fixture', ['remote'], indirect=True)
def test_verified_web_upload_can_import_but_unverified_source_cannot(pool, db, fixture):
    x = fixture
    store = PostgresRemoteInsightImports(pool)
    attempt = x.request
    wrong_hash = attempt.model_copy(update={'pdf_sha256': 'b' * 64})
    assert store.save(x.owner, x.device, x.context, wrong_hash) is None
    assert store.save('another-owner', x.device, x.context, attempt) is None
    assert store.save(x.owner, uuid4(), x.context, attempt) is None
    db("UPDATE server_remote_upload_intents SET status='failed' WHERE owner_user_id=%s", (x.owner,))
    assert store.save(x.owner, x.device, x.context, attempt) is None
    assert db('SELECT revision FROM server_remote_insight_collections WHERE context_id=%s', (x.context,)) == []
    db("UPDATE server_remote_upload_intents SET status='verified' WHERE owner_user_id=%s", (x.owner,))
    assert store.save(x.owner, x.device, x.context, attempt)['revision'] == 1
    assert store.save(x.owner, x.device, x.context, attempt)['revision'] == 1
    queries = RemoteProductQueries(pool)
    queries.insights_enabled = True
    queries.owner = lambda: x.owner
    detail = queries.detail(str(x.context))['result']
    assert detail['keywords'][0]['quote_original'] == attempt.items[0].quote_original
    assert detail['important_sentences'][0]['translation_revision_id'] == str(attempt.translation_revision_id)
