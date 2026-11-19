"""로컬 번역 리비전의 검색 조각만 다시 색인한다."""

from uuid import uuid4

from solo_leveling.domain.models import EmbeddingSet, RequestConflictError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.embeddings.embedder import embed_token_windows
from solo_leveling.infrastructure.storage.vector_store import (
    upsert_chunk_embeddings, verify_chunk_embeddings,
)


def reindex_local_fragments(db_path, client, version_id, base_embedding_set_id, *,
                            embed=embed_token_windows):
    base = repo.get_search_index(db_path, version_id)
    if base is None or base['embedding_set_id'] != base_embedding_set_id:
        raise RequestConflictError('지정한 기준 색인이 현재 기본 색인이 아닙니다.')
    chunks = repo.get_translated_chunks(db_path, base['parse_revision_id'], base['translation_revision_id'])
    if not chunks or len(chunks) != base['chunk_count']:
        raise ValueError('재색인할 번역 청크가 기존 색인과 일치하지 않습니다.')
    set_id = str(uuid4())
    windows, vectors = embed(chunks, set_id, model_name=base['model_name'])
    parents = {chunk.chunk_id: chunk.text for chunk in chunks}
    if ({window.parent_chunk_id for window in windows} != parents.keys()
            or any(window.embedding_set_id != set_id for window in windows)):
        raise ValueError('재색인 조각에 부모 청크가 누락되었습니다.')
    ids = [window.embedding_fragment_id for window in windows]
    documents = [parents[window.parent_chunk_id] for window in windows]
    if not vectors or len(vectors[0]) != base['dimension']:
        raise ValueError('재색인 모델 차원이 기존 색인과 일치하지 않습니다.')
    collection_name = f'chunks-{set_id}'
    # 새 컬렉션만 정리한다. 기존 색인은 모든 실패 경로에서 그대로 남는다.
    try:
        upsert_chunk_embeddings(client, ids, vectors, set_id, base['paper_id'], version_id, documents)
        verify_chunk_embeddings(client, ids, documents, set_id, base['paper_id'], version_id, base['dimension'])
        embedding_set = EmbeddingSet(set_id, base['translation_revision_id'], base['model_name'], base['dimension'])
        repo.publish_fragment_reindex(db_path, base, embedding_set, chunks, windows)
    except BaseException:
        # SQLite 커밋 직후 인터럽트가 발생해도 이미 게시된 컬렉션을 지우지 않는다.
        if repo.get_search_index(db_path, version_id, embedding_set_id=set_id) is None:
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
        raise
    return set_id
