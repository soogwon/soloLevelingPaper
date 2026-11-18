"""실제 비특권 연결로 학습·메모 복구와 금지된 쓰기를 확인한다."""
# ruff: noqa: F811
import hashlib
import os
from uuid import uuid4

import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, DSN  # noqa: F401
from tests.integration.test_remote_annotations_postgres import context, annotation  # noqa: F401
from solo_leveling.interfaces.cli.annotation_permissions import grant_annotation_role
from solo_leveling.interfaces.http.remote_annotations import PostgresRemoteAnnotationStore
from solo_leveling.interfaces.http.remote_learning import PostgresRemoteLearningStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_limited_role_full_existing_ports_and_denials(pool, db, context):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    role = 'annotation_test_' + uuid4().hex[:12]
    with pool.connection() as conn:
        schema = conn.execute('SELECT current_schema() s').fetchone()['s']
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-annotation-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_annotation_role(conn, schema, role)
        grant_annotation_role(conn, schema, role)
    restricted = ConnectionPool(make_conninfo(DSN, user=role, password='local-annotation-only'),
        kwargs={'row_factory': dict_row, 'options': f'-c search_path={schema}'}, open=True)
    try:
        owner, key, _ = context
        learning = PostgresRemoteLearningStore(restricted)
        original = learning.start(owner, key)
        assert learning.set_status(owner, key, status='completed', expected_revision=1)['revision'] == 2
        assert learning.start(owner, key)['learning_record_id'] == original['learning_record_id']
        store = PostgresRemoteAnnotationStore(restricted)
        value = annotation(context)
        saved = store.create(owner, 'product', key, 'key', value)
        assert store.create(owner, 'product', key, 'key', value).reused
        assert store.recover_request(owner, 'product', key, value).annotation == saved.annotation
        assert store.confirm_request(owner, 'product', key, 'key', saved.annotation.annotation_id, 1)
        assert store.lookup_request('other', 'product', key, 'key') is None
        assert store.mark_deleted(owner, saved.annotation.annotation_id)
        assert store.lookup_request(owner, 'product', key, 'key').state == 'deleted'
        for statement in (
            'SELECT * FROM web_sessions', 'SELECT * FROM server_device_credentials',
            'DELETE FROM server_context_annotations',
            'UPDATE server_papers SET paper_id=paper_id',
            'UPDATE server_execution_contexts SET context_id=context_id',
            'UPDATE server_processing_publications SET publication_id=publication_id',
            "UPDATE server_processing_chunks SET original_text='changed'",
            'UPDATE server_annotation_create_requests SET input_sha256=input_sha256',
            'UPDATE server_remote_learning_records SET context_id=context_id',
            'UPDATE server_dispatch_controls SET dispatch_enabled=true',
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with restricted.connection() as conn:
                    conn.execute(statement)
    finally:
        restricted.close()
        with pool.connection() as conn:
            trigger = 'annotation_lock_' + hashlib.sha256(role.encode()).hexdigest()[:12]
            for table in ('server_papers', 'server_execution_contexts', 'server_processing_publications'):
                conn.execute(sql.SQL('DROP TRIGGER {} ON {}.{}').format(
                    sql.Identifier(trigger), sql.Identifier(schema), sql.Identifier(table)))
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
