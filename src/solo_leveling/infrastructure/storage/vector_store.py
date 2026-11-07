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
import threading
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
_FAILED_MARK = '_solo_leveling_close_failed'
_LOCK_MARK = '_solo_leveling_close_lock'
_LOCK_GUARD = threading.Lock()
_UNATTACHABLE_LOCK = threading.RLock()  # 속성을 붙일 수 없는 객체용. 이런 객체끼리는 서로 직렬화된다.


def _close_lock(client):
    """클라이언트마다 하나의 종료 잠금. 처음 만드는 순간만 전역 보호 잠금 아래에서 한다."""
    with _LOCK_GUARD:
        lock = getattr(client, _LOCK_MARK, None)
        if lock is None:
            lock = threading.RLock()
            try:
                setattr(client, _LOCK_MARK, lock)
            except Exception:
                return _UNATTACHABLE_LOCK
        return lock


def close_client(client) -> ChromaCloseResult:
    """Chroma 클라이언트를 닫고 결과 코드를 돌려준다. 예외를 던지지 않는다.

    - 같은 클라이언트를 다시 닫으면 ALREADY_CLOSED(안전한 no-op)다.
    - close가 없는 클라이언트는 UNSUPPORTED이며 성공으로 간주하지 않는다.
    - 한 번 FAILED가 된 클라이언트는 다시 호출해도 FAILED로 남는다(#120). Chroma는 close()에서 '닫힘'
      표시를 먼저 하고 자원을 해제하므로, 해제가 중간에 실패한 뒤의 재호출은 아무것도 하지 않고 성공처럼
      돌아온다. 그 반환으로는 자원이 실제로 해제됐는지 알 수 없어서 CLOSED/ALREADY_CLOSED로 올리지 않는다.
      같은 경로를 쓰는 새 클라이언트를 만들거나 프로세스를 끝내는 것이 복구 방법이다.
    - 같은 클라이언트에 대한 종료 호출이 겹치면(여러 스레드) 먼저 들어온 호출의 결과가 확정될 때까지 나머지는 기다린다(#135).
      해제 도중인 클라이언트를 보고 다른 호출이 CLOSED/ALREADY_CLOSED를 돌려주지 않으며, 해제가 실패하면 기다린 호출과
      이후 재호출 모두 FAILED다. 객체별 잠금을 붙일 수 있는 클라이언트(Chroma 포함)는 다른 클라이언트의 종료를 서로 막지 않는다. 속성을 붙일 수 없는 객체끼리는 공통 잠금 때문에 직렬화된다.
      종료가 끝나지 않으면 기다리는 호출도 끝나지 않는다(타임아웃으로 성공·실패를 추측하지 않는다).
    - 같은 경로를 공유하는 다른 클라이언트나 전역 캐시는 건드리지 않는다
      (reset·캐시 삭제·강제 stop을 종료 수단으로 쓰지 않는다).
    호출자는 FAILED·UNSUPPORTED를 성공으로 보지 말고, 원래 작업의 오류를 이 결과로
    덮어쓰지 않아야 한다.

    한계: 속성을 붙일 수 없는 객체(__slots__ 등)는 실패·성공 표시를 기억하지 못한다. Chroma 클라이언트는 해당 없다.
    """
    if client is None:
        return ChromaCloseResult.ALREADY_CLOSED
    # 같은 클라이언트의 상태 확인·실제 종료·결과 기록을 한 잠금 아래에서 한다(#135). 종료가 진행 중일 때 들어온 다른 호출은
    # 그 결과가 확정될 때까지 기다리고, 확정된 결과(성공이면 ALREADY_CLOSED, 실패면 FAILED)를 그대로 본다.
    with _close_lock(client):
        if getattr(client, _CLOSED_MARK, False) is True:
            return ChromaCloseResult.ALREADY_CLOSED
        if getattr(client, _FAILED_MARK, False) is True:
            return ChromaCloseResult.FAILED
        close = getattr(client, 'close', None)
        if not callable(close):
            return ChromaCloseResult.UNSUPPORTED
        try:
            close()
        except Exception:
            try:
                setattr(client, _FAILED_MARK, True)
            except Exception:
                pass
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


def restore_legacy_index_memberships(db_path: str, client) -> int:
    """실제 Chroma 컬렉션으로 확인한 구버전 색인의 청크 소속만 복원한다.

    컬렉션 누락·개수 불일치·중복 ID·메타데이터 불일치는 미확인 상태로 남긴다. 현재 번역 결과는 게시 당시
    집합의 증거로 사용하지 않는다. 한 색인의 실패가 다른 정상 색인의 복원을 막지 않는다.
    """
    from solo_leveling.infrastructure.database import repository as repo

    restored = 0
    for index in repo.list_unconfirmed_search_indexes(db_path):
        try:
            collection = client.get_collection(f"chunks-{index['embedding_set_id']}")
            stored = collection.get(include=['metadatas'])
            ids = stored.get('ids')
            metadatas = stored.get('metadatas')
            if (not isinstance(ids, list) or not isinstance(metadatas, list)
                    or len(ids) != index['chunk_count'] or len(metadatas) != len(ids)
                    or len(set(ids)) != len(ids)
                    or any(type(chunk_id) is not str or not chunk_id.strip() for chunk_id in ids)):
                continue
            expected = {key: index[key] for key in ('embedding_set_id', 'paper_id', 'version_id')}
            if any(not isinstance(meta, dict) or any(meta.get(key) != value for key, value in expected.items())
                   for meta in metadatas):
                continue
            if not repo.confirm_search_index_chunks(db_path, index['embedding_set_id'], ids):
                continue
        except Exception:
            continue
        restored += 1
    return restored


def delete_by_embedding_set(client, embedding_set_id, collection_name=None) -> None:
    get_collection(client, collection_name or f'chunks-{embedding_set_id}').delete(where={'embedding_set_id': embedding_set_id})
