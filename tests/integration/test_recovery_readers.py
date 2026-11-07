"""복구 색인을 게시한 뒤에도 각 학습 맥락이 자신의 번역과 근거를 조회한다."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.search import ContextSearchService
from solo_leveling.application.paper_detail.service import PaperDetailService
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.paper_summary import SummaryItem
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.database.dashboard_queries import DashboardReader
from solo_leveling.infrastructure.retrieval.paper_detail import SQLitePaperDetailReader
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever, SQLiteContextReader
from solo_leveling.infrastructure.storage.vector_store import upsert_chunk_embeddings
from tests.unit.test_translation_recovery import db, emb, fail, ok, run_recovery, N, FAILED


@pytest.mark.parametrize('partial', [False, True], ids=['full_recovery', 'partial_recovery'])
def test_published_recovery_preserves_each_context(db, tmp_path, chroma_client_factory, partial):
    client = chroma_client_factory(str(tmp_path / 'chroma'))
    search = ContextSearchService(SQLiteContextReader(db), SQLiteChromaRetriever(
        db, client, embedder=lambda texts, **kwargs: [[1., 0., 0., 0.]]))
    detail = PaperDetailService(SQLitePaperDetailReader(db))
    dashboard = DashboardReader(db)

    def index(revision, index_id):
        chunks = repo.get_translated_chunks(db, 'p1', revision)
        upsert_chunk_embeddings(client, [c.chunk_id for c in chunks], [[1., 0., 0., 0.]] * len(chunks),
                                index_id, 'paper', 'v1', [c.text for c in chunks])

    index('t1', 'e1')
    old = repo.get_or_create_default_context(db, 'v1').context_id
    repo.save_evidences(db, old, [Evidence('old', 'c0', '번역 0', 'original 0')])
    repo.save_summary(db, old, 'paper', [SummaryItem('claim', '기존 주장', ('old',))])
    recovered_indices = (3,) if partial else FAILED
    run_recovery(db, items=[ok(i, '복구') if i in recovered_indices else fail(i) for i in FAILED])
    index('t2', 'e2')
    original_ids = {f'c{i}' for i in range(N) if i not in FAILED}
    recovered_ids = original_ids | {f'c{i}' for i in recovered_indices}
    repo.publish_recovered_index(db, 'j2', emb('e2', 't2'), recovered_ids, ['partial'] if partial else [])
    new = repo.get_or_create_default_context(db, 'v1').context_id
    assert new != old
    for ctx, expected_job, expected_ids in [(old, 'j1', original_ids), (new, 'j2', recovered_ids)]:
        assert dashboard.get(ctx)['processing']['job_id'] == expected_job
        hits = search.search('질문', ctx, N).items
        assert {h.chunk.chunk_id for h in hits} == expected_ids
        assert any(h.chunk.chunk_id == 'c3' and h.chunk.text == '복구 3' for h in hits) == (ctx == new)
    recovered = Evidence('new', 'c3', '복구 3', 'original 3')
    with pytest.raises(ValueError):
        repo.save_evidences(db, old, [recovered])
    repo.save_evidences(db, new, [recovered])
    repo.save_summary(db, new, 'paper', [SummaryItem('claim', '새 주장', ('new',))])
    for ctx, rev, quote in [(old, 't1', '번역 0'), (new, 't2', '복구 3')]:
        evidence, = detail.get(ctx)['evidence']
        assert (evidence['translation_revision_id'], evidence['quote_ko']) == (rev, quote)
        assert repo.get_evidences(db, ctx, [evidence['evidence_id']]).evidence[0].quote_ko == quote

    # 유효한 두 색인이라도 맥락에 지정한 번역과 조합이 다르면 검색을 중단한다.
    with closing(get_connection(db)) as conn, conn:
        conn.execute('UPDATE learning_contexts SET embedding_set_id=? WHERE context_id=?', ('e2', old))
    with pytest.raises(ValueError, match='검색 범위'):
        search.search('질문', old)
    with pytest.raises(ValueError, match='outside context'):
        repo.get_evidences(db, old, ['old'])
