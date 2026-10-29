"""
infrastructure/storage — ChromaDB 벡터 저장

22번 문서: "ChromaDB는 재구축 가능한 검색 파생 데이터다"
"SQLite와 Chroma 쓰기는 하나의 트랜잭션이 아니다. 파생 색인을 먼저 준비·검증한
후 SQLite의 ready 상태·활성 참조를 게시한다."
→ 이 모듈은 항상 workers/ingestion.py에서 SQLite ready 표시보다 먼저 호출된다.

"각 임베딩 설정·파싱 결과에 연결한 Chroma 컬렉션 또는 검증된 필터로 검색 범위를
제한한다" — embedding set별 컬렉션과 메타데이터 필터를 함께 사용한다.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
import math
from typing import List, Optional


def get_client(persist_dir: str):
    import chromadb
    Path(persist_dir).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=persist_dir)


class ChromaCloseResult(str, Enum):
    """close_client의 안전한 결과 코드. 예외 메시지·경로는 담지 않는다."""
    CLOSED = 'CHROMA_CLOSED'
    ALREADY_CLOSED = 'CHROMA_ALREADY_CLOSED'
    UNSUPPORTED = 'CHROMA_CLOSE_UNSUPPORTED'
    FAILED = 'CHROMA_CLOSE_FAILED'


_CLOSED_MARK = '_solo_leveling_closed'


def close_client(client) -> ChromaCloseResult:
    """Chroma 클라이언트를 닫고 결과 코드를 돌려준다. 예외를 던지지 않는다.

    - 같은 클라이언트를 다시 닫으면 ALREADY_CLOSED(안전한 no-op)다.
    - close가 없는 클라이언트는 UNSUPPORTED이며 성공으로 간주하지 않는다.
    - 같은 경로를 공유하는 다른 클라이언트나 전역 캐시는 건드리지 않는다
      (reset·캐시 삭제를 종료 수단으로 쓰지 않는다).
    호출자는 FAILED·UNSUPPORTED를 성공으로 보지 말고, 원래 작업의 오류를 이 결과로
    덮어쓰지 않아야 한다.
    """
    if client is None or getattr(client, _CLOSED_MARK, False) is True:
        return ChromaCloseResult.ALREADY_CLOSED
    close = getattr(client, 'close', None)
    if not callable(close):
        return ChromaCloseResult.UNSUPPORTED
    try:
        close()
    except Exception:
        return ChromaCloseResult.FAILED
    try:
        setattr(client, _CLOSED_MARK, True)
    except Exception:
        pass
    return ChromaCloseResult.CLOSED


def get_collection(client, name: str = "chunks"):
    return client.get_or_create_collection(name=name)


def upsert_chunk_embeddings(
    client,
    chunk_ids: List[str],
    embeddings: List[List[float]],
    embedding_set_id: str,
    paper_id: str,
    version_id: str,
    documents: List[str],
    collection_name: Optional[str] = None,
) -> None:
    """
    호출자는 번역 성공 청크만 전달한다. 길이와 벡터 값을 저장 전에 검사한다.
    """
    if not chunk_ids or not (len(chunk_ids) == len(embeddings) == len(documents)):
        raise ValueError('vector batch lengths must match and be nonempty')
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError('duplicate chunk IDs')
    dimension = len(embeddings[0])
    if not dimension or any(len(v) != dimension or any(not math.isfinite(x) for x in v) for v in embeddings):
        raise ValueError('invalid embedding dimensions or values')
    if any(not isinstance(doc, str) or not doc.strip() for doc in documents):
        raise ValueError('empty translated document')
    collection = get_collection(client, collection_name or f'chunks-{embedding_set_id}')
    metadatas = [
        {"embedding_set_id": embedding_set_id, "paper_id": paper_id, "version_id": version_id}
        for _ in chunk_ids
    ]
    collection.upsert(ids=chunk_ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def query_similar(
    client,
    query_embedding: List[float],
    embedding_set_id: str,
    top_k: int = 5,
    paper_id: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> List[dict]:
    """
    embedding_set_id로 검색 범위를 제한한다 — 질문과 청크가 다른 임베딩
    설정으로 만들어진 벡터끼리 섞여 비교되는 일을 막기 위함(22번 문서 원칙).
    """
    if type(top_k) is not int or top_k < 1:
        raise ValueError('top_k must be a positive integer')
    collection = get_collection(client, collection_name or f'chunks-{embedding_set_id}')
    where = {"embedding_set_id": embedding_set_id}
    if paper_id:
        where = {"$and": [{"embedding_set_id": embedding_set_id}, {"paper_id": paper_id}]}

    result = collection.query(query_embeddings=[query_embedding], n_results=top_k, where=where)
    if not result["ids"] or not result["ids"][0]:
        return []

    out = []
    for chunk_id, dist, doc in zip(result["ids"][0], result["distances"][0], result["documents"][0]):
        out.append({"chunk_id": chunk_id, "distance": dist, "similarity": 1 / (1 + dist), "document": doc})
    return out


def delete_by_version(client, version_id: str, collection_name: Optional[str] = None) -> None:
    """22번 문서: 삭제는 접근 차단 후 벡터 정리 순서. 재시도 시 고아 데이터 정리에도 사용."""
    if collection_name is not None:
        get_collection(client, collection_name).delete(where={'version_id': version_id})
        return
    for entry in client.list_collections():
        name = entry if isinstance(entry, str) else entry.name
        if name == 'chunks' or name.startswith('chunks-'):
            client.get_collection(name).delete(where={'version_id': version_id})


def verify_chunk_embeddings(client, chunk_ids, documents, embedding_set_id,
                            paper_id, version_id, dimension, collection_name=None) -> None:
    """저장된 청크 구성·번역문·메타데이터·벡터 차원을 다시 읽어 검증한다."""
    result = get_collection(client, collection_name or f'chunks-{embedding_set_id}').get(
        where={'embedding_set_id': embedding_set_id},
        include=['documents', 'metadatas', 'embeddings'],
    )
    ids = result['ids']
    if len(ids) != len(chunk_ids) or set(ids) != set(chunk_ids):
        raise ValueError('incomplete vector index')
    expected = dict(zip(chunk_ids, documents))
    for i, chunk_id in enumerate(ids):
        meta = result['metadatas'][i]
        if result['documents'][i] != expected[chunk_id] or any(meta.get(k) != v for k, v in {
            'embedding_set_id': embedding_set_id, 'paper_id': paper_id, 'version_id': version_id,
        }.items()):
            raise ValueError('vector index metadata mismatch')
        vector = result['embeddings'][i]
        if len(vector) != dimension or any(not math.isfinite(x) for x in vector):
            raise ValueError('invalid stored vector')


def delete_by_embedding_set(client, embedding_set_id, collection_name=None) -> None:
    get_collection(client, collection_name or f'chunks-{embedding_set_id}').delete(where={'embedding_set_id': embedding_set_id})
