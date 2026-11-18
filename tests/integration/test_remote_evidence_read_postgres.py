"""합성 완료 답변으로 원격 근거 재조회의 격리·무결성을 검증한다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
from solo_leveling.interfaces.http.remote_evidence_read import PostgresRemoteEvidenceReader

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_saved_evidence_is_pinned_readonly_and_owner_scoped(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    reader = PostgresRemoteEvidenceReader(pool)
    ctx, eid = prepared.binding.context_id, prepared.evidence[0].evidence_id
    result = reader.get('alice', ctx, eid)
    assert result['quote_original'] == prepared.evidence[0].quote_original
    assert result['publication_id'] == prepared.binding.publication_id
    assert result['embedding_set_id'] == prepared.binding.embedding_set_id
    assert len(result['original_text_sha256']) == 64
    assert result['pdf_page'] >= 1
    before = db('SELECT count(*) n FROM server_remote_executions')
    for _ in range(2):
        assert reader.get('alice', ctx, eid) == result
        assert reader.get('bob', ctx, eid) is None
        assert reader.get('alice', str(uuid4()), eid) is None
        assert reader.get('alice', ctx, str(uuid4())) is None
    assert db('SELECT count(*) n FROM server_remote_executions') == before
    assert not db('SELECT attempt_id FROM server_provider_attempts')
    # active pointer가 없어도 이미 고정한 publication의 근거를 재조회한다.
    db('UPDATE server_processing_versions SET active_publication_id=NULL WHERE owner_user_id=%s AND paper_id=%s',
       ('alice', prepared.binding.paper_id))
    assert reader.get('alice', ctx, eid) == result
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s', (prepared.binding.paper_id,))
    assert reader.get('alice', ctx, eid) is None


@pytest.mark.parametrize('target', ['payload', 'quote'])
def test_corrupt_evidence_or_answer_is_not_returned(db, stores, pool, target):
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    eid = prepared.evidence[0].evidence_id
    if target == 'payload':
        db("UPDATE server_remote_executions SET payload_json='{}' WHERE execution_id=%s", (intent.execution_id,))
    else:
        db("UPDATE server_processing_evidences SET quoted_text='잘못된 인용' WHERE evidence_id=%s", (eid,))
    with pytest.raises(ValueError, match='remote_evidence_integrity_invalid'):
        PostgresRemoteEvidenceReader(pool).get('alice', prepared.binding.context_id, eid)


def test_evidence_tool_through_authenticated_mcp(db, stores, pool):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from tests.integration.test_remote_product_queries_postgres import Verifier, result, SETTINGS
    from solo_leveling.interfaces.http import create_app
    from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries
    from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    queries = RemoteProductQueries(pool)
    queries.owners.link_identity('alice', VerifiedRemoteIdentity(SETTINGS.issuer, 'account-a'))
    args = {'context_id': prepared.binding.context_id, 'evidence_id': prepared.evidence[0].evidence_id}
    app = create_app(remote_mcp_settings=replace(SETTINGS, product_reads_enabled=True),
                     remote_mcp_verifier=Verifier(), remote_product_queries=queries)
    with TestClient(app, base_url=SETTINGS.resource_url[:-4]) as client:
        returned = result(client, 'get_remote_evidence', args)
        assert returned['status'] == 'ok'
        assert returned['result']['evidence_id'] == args['evidence_id']
        assert result(client, 'get_remote_evidence', args, token='account-b') == {'status': 'not_found'}
        queries.owners.revoke_identity('alice', VerifiedRemoteIdentity(SETTINGS.issuer, 'account-a'))
        assert result(client, 'get_remote_evidence', args) == {'status': 'not_found'}


def test_evidence_reader_only_needs_select_permissions(db, stores, pool):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from tests.integration.test_remote_processing_postgres import DSN
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    role = 'evidence_read_' + uuid4().hex[:12]
    tables = ('server_execution_contexts', 'server_papers', 'server_processing_publications',
              'server_embedding_sets', 'server_processing_evidences', 'server_remote_executions',
              'server_processing_chunks', 'server_translation_chunks')
    with pool.connection() as conn:
        schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-evidence-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
        for table in tables:
            conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
    restricted = ConnectionPool(make_conninfo(DSN, user=role, password='local-evidence-only'),
        kwargs={'row_factory': dict_row, 'options': f'-c search_path={schema}'}, open=True)
    try:
        result = PostgresRemoteEvidenceReader(restricted).get('alice', prepared.binding.context_id, prepared.evidence[0].evidence_id)
        assert result['publication_id'] == prepared.binding.publication_id
        for statement in ('DELETE FROM server_processing_evidences', 'SELECT * FROM web_sessions',
                          'UPDATE server_processing_chunks SET original_text=original_text'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with restricted.connection() as conn:
                    conn.execute(statement)
    finally:
        restricted.close()
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
