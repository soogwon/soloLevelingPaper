"""
infrastructure/storage — ChromaDB 벡터 저장

22번 문서: "ChromaDB는 재구축 가능한 검색 파생 데이터다"
"SQLite와 Chroma 쓰기는 하나의 트랜잭션이 아니다. 파생 색인을 먼저 준비·검증한
후 SQLite의 ready 상태·활성 참조를 게시한다."
→ 이 모듈은 항상 workers/ingestion.py에서 SQLite ready 표시보다 먼저 호출된다.

"각 임베딩 설정·파싱 결과에 연결한 Chroma 컬렉션 또는 검증된 필터로 검색 범위를
제한한다" — 여기서는 컬렉션 하나 + embedding_set_id 메타데이터 필터 방식을 쓴다.
"""
from pathlib import Path
from typing import List, Optional

import chromadb


def get_client(persist_dir: str) -> chromadb.ClientAPI:
    Path(persist_dir).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=persist_dir)


def get_collection(client: chromadb.ClientAPI, name: str = "chunks"):
    return client.get_or_create_collection(name=name)


def upsert_chunk_embeddings(
    client: chromadb.ClientAPI,
    chunk_ids: List[str],
    embeddings: List[List[float]],
    embedding_set_id: str,
    paper_id: str,
    version_id: str,
    documents: List[str],
    collection_name: str = "chunks",
) -> None:
    """
    text(번역문)가 아직 없는 시점(번역 전)에는 documents로 original_text를 넘겨도 된다 —
    이 함수는 어떤 텍스트를 임베딩했는지는 호출부 책임이고, 저장만 담당한다.
    """
    collection = get_collection(client, collection_name)
    metadatas = [
        {"embedding_set_id": embedding_set_id, "paper_id": paper_id, "version_id": version_id}
        for _ in chunk_ids
    ]
    collection.upsert(ids=chunk_ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def query_similar(
    client: chromadb.ClientAPI,
    query_embedding: List[float],
    embedding_set_id: str,
    top_k: int = 5,
    paper_id: Optional[str] = None,
    collection_name: str = "chunks",
) -> List[dict]:
    """
    embedding_set_id로 검색 범위를 제한한다 — 질문과 청크가 다른 임베딩
    설정으로 만들어진 벡터끼리 섞여 비교되는 일을 막기 위함(22번 문서 원칙).
    """
    collection = get_collection(client, collection_name)
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


def delete_by_version(client: chromadb.ClientAPI, version_id: str, collection_name: str = "chunks") -> None:
    """22번 문서: 삭제는 접근 차단 후 벡터 정리 순서. 재시도 시 고아 데이터 정리에도 사용."""
    collection = get_collection(client, collection_name)
    collection.delete(where={"version_id": version_id})
