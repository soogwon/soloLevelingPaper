"""저장 답변의 과거 게시 보존과 제품 tombstone 후 복구 차단을 연결한다."""
# ruff: noqa: F811
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from tests.integration.test_remote_answer_postgres import seeded
from solo_leveling.application.remote_replay import ExecutionLookup, read_existing_execution
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore, RemoteAnswerSaveRejected
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def lookup(intent, prepared):
    return ExecutionLookup('alice', 'ask', intent.execution_id, 'input', prepared.binding.context_id)


def test_republish_preserves_saved_answer_then_tombstone_blocks_replay(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)
    saved = PostgresRemoteAnswerStore(pool).save(intent, prepared)
    reader = PostgresRemoteExecutionStore(pool)
    key = lookup(intent, prepared)
    before = db('SELECT row_to_json(e)::text AS value FROM server_processing_evidences e WHERE execution_id=%s ORDER BY evidence_id', (intent.execution_id,))
    _, _, newer = seeded(db, stores, pool, paper=prepared.binding.paper_id)
    assert newer.publication_id != prepared.binding.publication_id
    restored = read_existing_execution(reader, key)
    assert restored.execution_id == intent.execution_id and restored.payload == saved
    assert restored.payload['context_id'] == prepared.binding.context_id
    assert {c['translation_revision_id'] for c in restored.payload['citations']} == {prepared.binding.translation_revision_id}
    assert db('SELECT row_to_json(e)::text AS value FROM server_processing_evidences e WHERE execution_id=%s ORDER BY evidence_id', (intent.execution_id,)) == before
    assert reader.lookup('bob', 'ask', intent.execution_id) is None
    deletion = PostgresRemoteDeletionStore(pool).request('alice', prepared.binding.paper_id, new_id())
    assert deletion.status == 'blocking'
    assert read_existing_execution(reader, key) is None
    assert db('SELECT count(*) AS n FROM server_provider_attempts WHERE execution_id=%s', (intent.execution_id,))[0]['n'] == 0
    # 접근 차단과 물리 삭제를 혼동하지 않는다. 저장 행은 아직 남아 있다.
    assert db('SELECT count(*) AS n FROM server_processing_evidences WHERE execution_id=%s', (intent.execution_id,))[0]['n'] == len(prepared.evidence)


def test_late_prepared_answer_after_tombstone_never_creates_evidence(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteDeletionStore(pool).request('alice', prepared.binding.paper_id, new_id())
    with pytest.raises(RemoteAnswerSaveRejected):
        PostgresRemoteAnswerStore(pool).save(intent, prepared)
    assert db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s', (intent.execution_id,)) == []
    row = db('SELECT state,payload_json FROM server_remote_executions WHERE execution_id=%s', (intent.execution_id,))[0]
    assert row['state'] != 'completed' and row['payload_json'] is None
    assert read_existing_execution(PostgresRemoteExecutionStore(pool), lookup(intent, prepared)) is None
