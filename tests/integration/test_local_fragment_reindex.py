"""SQLite와 Chroma에 분할 색인을 게시하고 이전 근거를 다시 읽는다."""

from contextlib import closing
from types import SimpleNamespace

import pytest

from solo_leveling.application.server_sync.resume import _available_search_data
from solo_leveling.domain.embedding_windows import TokenWindow
from solo_leveling.domain.models import Evidence, RequestConflictError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.workers.local_reindex import reindex_local_fragments
from tests.fixtures.chroma_retrieval_contract import ChromaContractStore


@pytest.fixture
def published(tmp_path, chroma_client_factory):
    store = ChromaContractStore(tmp_path / 'papers.sqlite3', chroma_client_factory(str(tmp_path / 'vectors')))
    return store, store.publish(partial=True)


def fragment_vectors(chunks, set_id, *, model_name):
    windows = tuple(
        TokenWindow(f'{chunk.chunk_id}:{start}', set_id, chunk.chunk_id, start, start + 112, (1,))
        for chunk in chunks for start in (0, 88)
    )
    return windows, [[1.0, 0.0] for _ in windows]


def test_reindex_keeps_old_context_evidence_and_search(published):
    store, old = published
    parent = next(row for row in old.rows if row.translated)
    repo.save_evidences(store.db, old.context, [Evidence('saved', parent.chunk_id, quote_ko=parent.korean, quote_original=parent.original)])
    before = repo.get_evidences(store.db, old.context, ['saved'])
    case = SimpleNamespace(top_k=20, pages=(), sections=())
    old_results = store.search(old, case)

    set_id = reindex_local_fragments(store.db, store.client, old.version, old.embedding, embed=fragment_vectors)

    current = repo.get_or_create_default_context(store.db, old.version)
    assert current.embedding_set_id == set_id
    assert current.parse_revision_id == old.parse and current.translation_revision_id == old.translation
    assert repo.get_learning_context(store.db, old.context).embedding_set_id == old.embedding
    assert repo.get_evidences(store.db, old.context, ['saved']) == before
    assert store.search(old, case) == old_results
    fresh = store.service.search('검증', current.context_id, 20)
    assert {item.chunk.chunk_id for item in fresh.items} == {r.chunk_id for r in old.rows if r.translated}
    assert len(fresh.items) == 7
    assert _available_search_data(store.db, store.client, 'paper', current.context_id, old.version)
    with closing(repo.get_connection(store.db)) as db:
        assert db.execute('SELECT count(*) FROM local_embedding_fragments').fetchone()[0] == 14
    assert store.client.get_collection(f'chunks-{set_id}').count() == 14
    assert store.client.get_collection(f'chunks-{old.embedding}').count() == 7
    with pytest.raises(RequestConflictError):
        reindex_local_fragments(store.db, store.client, old.version, old.embedding, embed=fragment_vectors)


def test_concurrent_publication_discards_only_losing_collection(published):
    store, old = published
    published_ids = []

    def publish_first(chunks, set_id, *, model_name):
        published_ids.append(reindex_local_fragments(
            store.db, store.client, old.version, old.embedding, embed=fragment_vectors))
        return fragment_vectors(chunks, set_id, model_name=model_name)

    with pytest.raises(RequestConflictError):
        reindex_local_fragments(store.db, store.client, old.version, old.embedding, embed=publish_first)

    winner, = published_ids
    assert store.active_embedding(old) == winner
    assert {entry.name for entry in store.client.list_collections()} == {
        f'chunks-{old.embedding}', f'chunks-{winner}',
    }
