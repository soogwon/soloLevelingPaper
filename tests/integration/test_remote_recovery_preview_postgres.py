"""합성 게시본에서 복구 대상 집계·과거 판본·계정 격리와 무변경을 확인한다."""
# ruff: noqa: F811
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore
from tests.integration.test_remote_product_queries_postgres import Verifier, result
from tests.integration.test_remote_mcp_product_boundary import SETTINGS
from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='로컬 PostgreSQL 필요')


def test_preview_counts_pinned_versions_and_no_writes(db, stores, pool, monkeypatch):
    remote = PgvectorContractStore(db, stores, pool)
    from tests.fixtures import pgvector_retrieval_contract as fixture
    original_ready = fixture.mark_ready
    def ready_with_statuses(db, parse, translation, embedding):
        # 게시 전 합성 상태를 만든다. 게시 후 불변성 트리거를 우회하지 않는다.
        chunks = db('SELECT chunk_id FROM server_processing_chunks WHERE parse_revision_id=%s ORDER BY ordinal', (parse,))
        for chunk, status in zip(chunks, ('translation_failed', 'original_only',
                                         'reference_content', 'translation_not_required')):
            db('UPDATE server_translation_chunks SET translation_status=%s WHERE translation_revision_id=%s AND chunk_id=%s',
               (status, translation, chunk['chunk_id']))
        original_ready(db, parse, translation, embedding)
    with monkeypatch.context() as patch:
        patch.setattr(fixture, 'mark_ready', ready_with_statuses)
        first = remote.publish(partial=True)
    second = remote.publish(100, base=first)
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성')", (remote.owner,))
    queries = RemoteProductQueries(pool)
    queries.owners.link_identity(remote.owner, VerifiedRemoteIdentity(SETTINGS.issuer, 'account-a'))
    def snapshot():
        # 조회가 어느 테이블에도 행을 쓰지 않는지 확인한다.
        tables = [r['tablename'] for r in db("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")]
        return {t: db(f'SELECT count(*) AS n FROM "{t}"')[0]['n'] for t in tables}
    before = snapshot()
    with TestClient(create_app(remote_mcp_settings=replace(SETTINGS, product_reads_enabled=True),
                              remote_mcp_verifier=Verifier(), remote_product_queries=queries),
                    base_url=SETTINGS.resource_url[:-4]) as client:
        args = {'context_id': first.context, 'operation': 'translation_recovery'}
        detail = result(client, 'get_remote_paper_detail', {'context_id': first.context})['result']
        assert detail['reprocessing_preview']['tool'] == 'preview_remote_reprocessing'
        view = result(client, 'preview_remote_reprocessing', args)
        assert view['status'] == 'ok'
        value = view['result']
        assert value['publication_id'] == first.publication
        assert value['embedding_set_id'] == first.embedding
        assert value['target_count'] == 3
        assert value['source_counts']['missing_chunks'] == 1
        assert value['source_counts']['failed_chunks'] == 1
        assert value['source_counts']['original_only_chunks'] == 1
        assert value['estimated_cost']['amount'] is None
        assert not value['submission_available'] and not value['transfer_preview']['sent_now']
        assert result(client, 'preview_remote_reprocessing', args) == view
        assert result(client, 'preview_remote_reprocessing', args, token='account-b') == {'status': 'not_found'}
        reparse = result(client, 'preview_remote_reprocessing', {**args, 'operation': 'reparse'})['result']
        assert reparse['target_count'] == 1 and reparse['target_unit'] == 'pdf_version'
        complete = result(client, 'preview_remote_reprocessing', {**args, 'context_id': second.context})['result']
        assert complete['target_count'] == 0 and complete['target_status'] == 'no_targets'
        assert snapshot() == before
        assert remote.active_embedding(first) == second.embedding
        db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (first.paper,))
        assert result(client, 'preview_remote_reprocessing', args) == {'status': 'not_found'}
