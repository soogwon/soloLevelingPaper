"""SQLite를 기준으로 범위를 검증하고 Chroma에서 한국어 번역문을 검색한다."""

from contextlib import closing
import math

from solo_leveling.application.evidence_qa.validators import validate_chunk, validate_search_result
from solo_leveling.domain.evidence_qa import (
    RetrievedChunk, RetrievalMethod, SearchResult, SearchScope, positive_int, require_text,
)
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.embeddings.embedder import embed_texts


class SQLiteContextReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_context(self, context_id: str):
        return repo.get_learning_context(self.db_path, context_id)

    def get_or_create_default_context(self, version_id: str):
        return repo.get_or_create_default_context(self.db_path, version_id)


class SQLiteChromaRetriever:
    def __init__(self, db_path: str, client, *, embedder=embed_texts):
        self.db_path = db_path
        self.client = client
        self.embedder = embedder

    def _snapshot(self, scope: SearchScope):
        # 논문·파싱·번역·작업의 연결과 청크를 같은 DB 스냅샷에서 확인한다.
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute('BEGIN')
            row = conn.execute('''
                SELECT s.*, v.paper_id, e.model_name, e.dimension
                FROM search_indexes s
                JOIN paper_versions v ON v.version_id=s.version_id
                JOIN parse_revisions p ON p.parse_revision_id=s.parse_revision_id AND p.version_id=v.version_id
                JOIN translation_revisions t ON t.translation_revision_id=s.translation_revision_id
                    AND t.parse_revision_id=p.parse_revision_id
                JOIN embedding_sets e ON e.embedding_set_id=s.embedding_set_id
                    AND e.translation_revision_id=t.translation_revision_id
                JOIN processing_jobs j ON j.job_id=s.job_id AND j.version_id=v.version_id AND j.status='ready'
                WHERE s.version_id=? AND s.parse_revision_id=? AND s.translation_revision_id=?
                ''', (scope.version_id, scope.parse_revision_id, scope.translation_revision_id)).fetchone()
            if row is None:
                raise ValueError('검색 범위에 일치하는 게시 완료 색인이 없습니다.')
            chunks = [Chunk(**dict(c)) for c in conn.execute('''
                SELECT * FROM chunks WHERE parse_revision_id=? AND translation_revision_id=? AND text IS NOT NULL
                ''', (scope.parse_revision_id, scope.translation_revision_id))]
        index = dict(row)
        positive_int(index['dimension'], 'dimension')
        require_text(index['model_name'], 'model_name')
        if not chunks or len(chunks) != index['chunk_count']:
            raise ValueError('DB의 번역 청크 수와 게시된 색인이 일치하지 않습니다.')
        full_scope = SearchScope(scope.version_id, scope.parse_revision_id, scope.translation_revision_id)
        for chunk in chunks:
            validate_chunk(chunk, full_scope)
        return index, {c.chunk_id: c for c in chunks}

    @staticmethod
    def _verify_vectors(collection, chunks, index):
        # 검색 실패와 정상적인 빈 결과를 구분하기 위해 게시된 색인 전체를 확인한다.
        stored = collection.get(include=['documents', 'metadatas', 'embeddings'])
        ids = stored['ids']
        if len(ids) != len(chunks) or set(ids) != set(chunks):
            raise ValueError('벡터 색인의 청크 구성과 DB가 일치하지 않습니다.')
        expected = {key: index[key] for key in ('embedding_set_id', 'paper_id', 'version_id')}
        for i, chunk_id in enumerate(ids):
            meta = stored['metadatas'][i] or {}
            if stored['documents'][i] != chunks[chunk_id].text or any(meta.get(k) != v for k, v in expected.items()):
                raise ValueError('벡터 색인의 번역문 또는 메타데이터가 DB와 일치하지 않습니다.')
            vector = stored['embeddings'][i]
            if len(vector) != index['dimension'] or any(not math.isfinite(x) for x in vector):
                raise ValueError('저장된 벡터의 차원 또는 값이 올바르지 않습니다.')

    def search(self, question: str, scope: SearchScope, top_k: int) -> SearchResult:
        require_text(question, 'question')
        positive_int(top_k, 'top_k')
        if not isinstance(scope, SearchScope):
            raise ValueError('검색 범위가 올바르지 않습니다.')
        require_text(scope.translation_revision_id, 'translation_revision_id')
        index, chunks = self._snapshot(scope)
        # 조회 중 누락된 컬렉션을 새로 만들지 않는다.
        collection = self.client.get_collection(f"chunks-{index['embedding_set_id']}")
        self._verify_vectors(collection, chunks, index)
        eligible = {key: c for key, c in chunks.items()
                    if (not scope.pdf_pages or c.pdf_page in scope.pdf_pages)
                    and (not scope.section_ids or c.section_id in scope.section_ids)}
        items = ()
        if eligible:
            vectors = self.embedder([question], model_name=index['model_name'])
            if len(vectors) != 1 or len(vectors[0]) != index['dimension'] or any(not math.isfinite(x) for x in vectors[0]):
                raise ValueError('질문 벡터의 차원 또는 값이 색인과 일치하지 않습니다.')
            # 범위 밖 상위 결과 때문에 범위 안 결과가 누락되지 않도록 전체 후보를 검색한다.
            # 초기 소규모 구현이며, 대규모 색인의 사전 필터·페이지 처리는 후속 최적화다.
            hits = collection.query(query_embeddings=vectors, n_results=len(chunks),
                where={'$and': [{'embedding_set_id': index['embedding_set_id']},
                                {'paper_id': index['paper_id']}, {'version_id': scope.version_id}]},
                include=['documents', 'distances'])
            ids, docs, distances = hits['ids'][0], hits['documents'][0], hits['distances'][0]
            if len(ids) != len(chunks) or set(ids) != set(chunks) or len(docs) != len(ids) or len(distances) != len(ids):
                raise ValueError('검색 중 벡터 색인 구성이 변경되었거나 결과가 누락되었습니다.')
            ranked = []
            for chunk_id, doc, distance in zip(ids, docs, distances):
                if doc != chunks[chunk_id].text or not math.isfinite(distance) or distance < 0:
                    raise ValueError('검색 결과의 번역문 또는 거리가 올바르지 않습니다.')
                if chunk_id in eligible:
                    ranked.append((chunk_id, 1.0 / (1.0 + distance)))
            ranked.sort(key=lambda hit: (-hit[1], hit[0]))
            items = tuple(RetrievedChunk(eligible[key], score, rank)
                          for rank, (key, score) in enumerate(ranked[:top_k], 1))
        result = SearchResult(question, scope, RetrievalMethod.VECTOR, index['embedding_set_id'], items)
        validate_search_result(result)
        return result
