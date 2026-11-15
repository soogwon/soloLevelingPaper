"""실제 DB 검색→B 답변 준비. 인증·source reader·생성/판정은 명시적 대역이다."""
# ruff: noqa: F811
import os
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed
from solo_leveling.application.evidence_qa.remote_answer import RemoteAnswerPreparationService
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters, RemoteRetrievalContractError
from solo_leveling.application.evidence_qa.remote_search import (
    RemoteContextSearchService, FingerprintCheckedEmbedder, QueryEmbedding,
)
from solo_leveling.application.evidence_qa.remote_sources import RemoteSourceChunk
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.semantic_verification import SemanticVerdict as V, SemanticReason as R
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.generation.fake_semantic_verifier import FakeSemanticVerifier
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_vectors import PostgresRemoteScopedSearch
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore


pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),
                                reason='실제 PostgreSQL+pgvector 테스트 DSN 필요')
FP = dict(model_id='synthetic', model_revision='r', tokenizer_id='t', tokenizer_revision='r',
          pooling='mean', normalization='l2', max_length=128, dimension=2, distance='cosine',
          runtime='pytorch', runtime_version='fixture', precision='fp32', artifact_sha256='a'*64)
FILTERS = RemoteSearchFilters((6,), ('s4',))


def seeded(db, stores, pool, owner='alice', *, paper=None):
    return seed(db, stores, pool, owner, paper=paper, fingerprint=FP,
                texts=('Synthetic original.', '합성 원문이다.'))


class FixtureSourceReader:
    """선택한 DB 행의 본문만 읽는 테스트 대역. A의 snapshot/권한/게시 집합 adapter가 아니다."""
    def __init__(self, db):
        self.db, self.reads = db, 0

    def read_chunks(self, binding, ids):
        self.reads += 1
        rows = self.db('''SELECT c.chunk_id,c.ordinal,c.pdf_page,c.section_id,c.original_text,t.translated_text
            FROM server_processing_chunks c JOIN server_translation_chunks t
              ON t.owner_user_id=c.owner_user_id AND t.chunk_id=c.chunk_id AND t.parse_revision_id=c.parse_revision_id
            WHERE c.owner_user_id=%s AND c.parse_revision_id=%s AND t.translation_revision_id=%s
              AND c.chunk_id=ANY(%s::uuid[])''',
            (binding.owner_user_id, binding.parse_revision_id, binding.translation_revision_id, list(ids)))
        return tuple(RemoteSourceChunk(binding, str(r['chunk_id']), r['ordinal'], r['pdf_page'], r['section_id'],
                                      r['original_text'], r['translated_text']) for r in rows)

    def following_chunks(self, *_):
        pytest.fail('완결 문장 fixture는 인접 조회를 하지 않아야 함')


def evaluator(db, pool, owner='alice', *, runtime=None, generator=None, semantic=None):
    reader = FixtureSourceReader(db)
    runtime = runtime or NS(embed=Mock(side_effect=lambda q, b: QueryEmbedding((1., 0.), b.model_fingerprint)))
    resolver = PostgresRemoteContextResolver(pool)
    search = RemoteContextSearchService(lambda ctx: resolver.resolve(owner, ctx),
        PostgresRemoteScopedSearch(pool, FingerprintCheckedEmbedder(runtime)))
    return RemoteAnswerPreparationService(search, reader, generator or FakeClaimGenerator(),
                                          semantic_verification=semantic), reader, runtime


def evidence_count(db):
    return db('SELECT count(*) AS n FROM server_processing_evidences')[0]['n']


def test_db_prefilter_through_answer_preserves_source_and_does_not_persist(db, stores, pool):
    _, revision, binding = seeded(db, stores, pool)
    seeded(db, stores, pool, 'bob')
    before = evidence_count(db)
    service, reader, _ = evaluator(db, pool)
    prepared = service.prepare('합성 설명은?', binding.context_id, filters=FILTERS, top_k=1)
    assert prepared.persistence_status == 'not_saved' and prepared.binding == binding
    assert prepared.response.result.status.value == 'ok'
    assert {e.chunk_id for e in prepared.evidence} == {revision[3][0]}
    assert prepared.evidence[0].quote_original == 'Synthetic original.'
    assert prepared.evidence[0].quote_ko == '합성 원문이다.'
    assert {c.chunk_id for c in prepared.response.result.citations} == {revision[3][0]}
    assert reader.reads == 1 and evidence_count(db) == before


def test_new_publication_does_not_change_prepared_old_context(db, stores, pool):
    paper, old_revision, old = seeded(db, stores, pool)
    service, _, _ = evaluator(db, pool)
    before = service.prepare('합성 설명은?', old.context_id, filters=FILTERS, top_k=1)
    _, new_revision, new = seeded(db, stores, pool, paper=paper)
    after = service.prepare('합성 설명은?', old.context_id, filters=FILTERS, top_k=1)
    assert new.publication_id != old.publication_id and after.binding == before.binding == old
    assert {e.chunk_id for e in after.evidence} == {old_revision[3][0]}
    assert not {e.chunk_id for e in after.evidence} & set(new_revision[3])
    assert [(e.chunk_id, e.quote_original, e.quote_ko) for e in after.evidence] == [
        (e.chunk_id, e.quote_original, e.quote_ko) for e in before.evidence]


def test_actual_tombstone_blocks_preparation_before_embedding_source_and_generation(db, stores, pool):
    paper, _, binding = seeded(db, stores, pool)
    gen = NS(generate_claims=Mock(side_effect=AssertionError('must not generate')))
    service, reader, runtime = evaluator(db, pool, generator=gen)
    request = PostgresRemoteDeletionStore(pool).request('alice', paper, new_id())
    assert request.status == 'blocking'
    # 물리 삭제 전에도 차단되어야 하므로 DB 행이 남아 있음을 함께 확인한다.
    assert db('SELECT count(*) AS n FROM server_processing_chunks WHERE paper_id=%s', (paper,))[0]['n'] > 0
    with pytest.raises(RemoteRetrievalContractError, match='^REMOTE_CONTEXT_NOT_FOUND$'):
        service.prepare('합성 설명은?', binding.context_id, filters=FILTERS)
    runtime.embed.assert_not_called()
    gen.generate_claims.assert_not_called()
    assert reader.reads == 0


def test_foreign_and_missing_context_have_same_failure_without_generation(db, stores, pool):
    _, _, binding = seeded(db, stores, pool)
    gen = NS(generate_claims=Mock(side_effect=AssertionError('must not generate')))
    service, reader, runtime = evaluator(db, pool, 'bob', generator=gen)
    for context in (binding.context_id, new_id()):
        with pytest.raises(RemoteRetrievalContractError, match='^REMOTE_CONTEXT_NOT_FOUND$'):
            service.prepare('합성 설명은?', context)
    assert reader.reads == 0
    runtime.embed.assert_not_called()
    gen.generate_claims.assert_not_called()


def test_same_dimension_foreign_runtime_blocks_answer_before_source(db, stores, pool):
    _, _, binding = seeded(db, stores, pool)
    runtime = NS(embed=Mock(return_value=QueryEmbedding((1., 0.), binding.model_fingerprint.replace('pytorch', 'onnx'))))
    gen = NS(generate_claims=Mock(side_effect=AssertionError('must not generate')))
    service, reader, _ = evaluator(db, pool, runtime=runtime, generator=gen)
    with pytest.raises(RemoteRetrievalContractError):
        service.prepare('합성 설명은?', binding.context_id, filters=FILTERS)
    assert runtime.embed.call_count == 1 and reader.reads == 0
    gen.generate_claims.assert_not_called()


def test_semantic_rejection_keeps_db_evidence_unchanged(db, stores, pool):
    _, _, binding = seeded(db, stores, pool)
    before = evidence_count(db)
    verifier = FakeSemanticVerifier(((V.CONTRADICTED, R.CONTRADICTION),))
    service, _, _ = evaluator(db, pool, semantic=SemanticVerificationService(verifier))
    result = service.prepare('합성 설명은?', binding.context_id, filters=FILTERS, top_k=1)
    assert len(verifier.calls) == 1
    assert result.response.result.claims == () and result.evidence == ()
    assert result.response.result.status.value == 'insufficient_evidence'
    assert evidence_count(db) == before
