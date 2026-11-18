"""근거 청크 필터를 커서보다 먼저 적용하고 다른 판본·맥락을 숨긴다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_annotations_postgres import pool, db, stores, context, annotation  # noqa: F401
from solo_leveling.interfaces.http.remote_annotations import PostgresRemoteAnnotationStore, RemoteAnnotationInput

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_filtered_cursor_and_revision_isolation(pool, db, context):
    owner, ctx, revisions = context
    store = PostgresRemoteAnnotationStore(pool)
    evidence = dict(context_id=ctx, chunk_id=revisions[3][0], parse_revision_id=revisions[0], translation_revision_id=revisions[1])
    expected = set()
    for i in range(23):
        value = annotation(context, target='translation' if i % 2 else 'original')
        expected.add(store.create(owner, 'test', ctx, f'linked-{i}', value).annotation.annotation_id)
        store.create(owner, 'test', ctx, f'unlinked-{i}', RemoteAnnotationInput('note', '맥락 전체 메모', ()))
    seen, after = [], None
    while True:
        page = store.list_annotations(owner, ctx, after=after, limit=7, evidence=evidence)
        seen += [a.annotation_id for a in page['items']]
        after = page['next_cursor']
        if after is None:
            break
    assert len(seen) == len(set(seen)) == 23 and set(seen) == expected
    assert store.list_annotations('other', ctx, evidence=evidence) is None
    for field in ('chunk_id', 'parse_revision_id'):
        assert store.list_annotations(owner, ctx, evidence={**evidence, field:str(uuid4())})['items'] == []
    page = store.list_annotations(owner, ctx, limit=50, evidence={**evidence, 'translation_revision_id':str(uuid4())})
    assert len(page['items']) == 12
    removed = next(iter(expected))
    store.delete_annotation(owner, ctx, removed, expected_revision=1)
    assert removed not in {a.annotation_id for a in store.list_annotations(owner, ctx, limit=50, evidence=evidence)['items']}
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s', (owner,))
    assert store.list_annotations(owner, ctx, evidence=evidence) is None


def test_actual_saved_evidence_to_annotation_card(monkeypatch, db, stores, pool):
    from types import SimpleNamespace
    from tests.integration.test_remote_answer_store_postgres import setup_answer
    from tests.unit.test_remote_annotation_tools import Server, token
    from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
    from solo_leveling.interfaces.http.remote_evidence_read import PostgresRemoteEvidenceReader
    from solo_leveling.interfaces.http.remote_annotation_recovery_routes import AnnotationRecoveryService
    from solo_leveling.interfaces.mcp import remote_annotations as module
    from solo_leveling.domain.annotation import SegmentInput
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    ctx, eid = prepared.binding.context_id, prepared.evidence[0].evidence_id
    reader = PostgresRemoteEvidenceReader(pool)
    evidence = reader.get('alice', ctx, eid)
    source = db('SELECT original_text FROM server_processing_chunks WHERE chunk_id=%s', (evidence['chunk_id'],))[0]['original_text']
    store = PostgresRemoteAnnotationStore(pool)
    saved = store.create('alice', 'product', ctx, 'evidence-linked', RemoteAnnotationInput('highlight', '', (
        SegmentInput(evidence['chunk_id'], 0, len(source), source, 'original', None),))).annotation
    monkeypatch.setattr(module, 'get_access_token', lambda: token(['annotations:read']))
    server = Server()
    module.register_annotation_tools(server, SimpleNamespace(owner=lambda: 'alice', evidences=reader), AnnotationRecoveryService(store, 'product'))
    result = server.tools['show_remote_evidence_detail'](ctx, eid)
    assert result['status'] == 'ok'
    assert result['result']['annotation_page']['items'][0]['annotation_id'] == saved.annotation_id
    assert result['result']['evidence']['publication_id'] == prepared.binding.publication_id
    assert not db('SELECT attempt_id FROM server_provider_attempts')
