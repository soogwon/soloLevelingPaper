"""고정 원격 context 범위에서 pgvector 후보를 먼저 제한해 검색한다."""

import json
import math
from dataclasses import dataclass
from typing import Callable, Sequence

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteSearchBinding,
    RemoteSearchFilters,
    RemoteSearchHit,
)


def _fingerprint_dimension(binding: RemoteSearchBinding) -> int:
    try:
        fingerprint = json.loads(binding.model_fingerprint)
        dimension = fingerprint["dimension"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValueError("remote_model_fingerprint_invalid") from None
    if type(dimension) is not int or not 1 <= dimension <= 4096:
        raise ValueError("remote_model_fingerprint_invalid")
    return dimension


def _vector_literal(values: Sequence[float], dimension: int) -> str:
    if (
        not isinstance(values, (list, tuple))
        or len(values) != dimension
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
    ):
        raise ValueError("remote_embedding_invalid")
    return "[" + ",".join(format(float(value), ".17g") for value in values) + "]"


class PostgresRemoteScopedSearch:
    """owner·게시 revision·embedding set·focus를 SQL에서 top_k 전에 제한한다."""

    def __init__(self, connection_pool, embed_question: Callable[[str, RemoteSearchBinding], Sequence[float]]):
        self._pool = connection_pool
        self._embed_question = embed_question

    def search(
        self,
        question: str,
        binding: RemoteSearchBinding,
        filters: RemoteSearchFilters,
        top_k: int,
    ) -> tuple[RemoteSearchHit, ...]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("remote_question_invalid")
        if not isinstance(binding, RemoteSearchBinding) or not isinstance(filters, RemoteSearchFilters):
            raise ValueError("remote_search_scope_invalid")
        if type(top_k) is not int or not 1 <= top_k <= 100:
            raise ValueError("remote_top_k_invalid")
        query_vector = _vector_literal(self._embed_question(question, binding), _fingerprint_dimension(binding))
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT c.chunk_id, c.pdf_page, c.section_id,
                          (v.embedding <=> %s::vector) AS cosine_distance
                   FROM server_embedding_vectors v
                   JOIN server_processing_chunks c
                     ON c.owner_user_id=v.owner_user_id
                    AND c.paper_id=v.paper_id
                    AND c.server_version_id=v.server_version_id
                    AND c.parse_revision_id=v.parse_revision_id
                    AND c.chunk_id=v.chunk_id
                   JOIN server_translation_chunks t
                     ON t.owner_user_id=v.owner_user_id
                    AND t.paper_id=v.paper_id
                    AND t.server_version_id=v.server_version_id
                    AND t.parse_revision_id=v.parse_revision_id
                    AND t.translation_revision_id=v.translation_revision_id
                    AND t.chunk_id=v.chunk_id
                   WHERE v.owner_user_id=%s
                     AND v.paper_id=%s AND v.server_version_id=%s
                     AND v.parse_revision_id=%s AND v.translation_revision_id=%s
                     AND v.embedding_set_id=%s
                     AND (cardinality(%s::integer[]) = 0 OR c.pdf_page = ANY(%s::integer[]))
                     AND (cardinality(%s::text[]) = 0 OR c.section_id = ANY(%s::text[]))
                   ORDER BY v.embedding <=> %s::vector, c.chunk_id
                   LIMIT %s""",
                (
                    query_vector,
                    binding.owner_user_id,
                    binding.paper_id,
                    binding.server_version_id,
                    binding.parse_revision_id,
                    binding.translation_revision_id,
                    binding.embedding_set_id,
                    list(filters.pdf_pages),
                    list(filters.pdf_pages),
                    list(filters.section_ids),
                    list(filters.section_ids),
                    query_vector,
                    top_k,
                ),
            ).fetchall()
        return tuple(
            RemoteSearchHit(
                binding=binding,
                chunk_id=str(row["chunk_id"]),
                pdf_page=int(row["pdf_page"]),
                section_id=None if row["section_id"] is None else str(row["section_id"]),
                cosine_distance=float(row["cosine_distance"]),
            )
            for row in rows
        )


class PostgresEmbeddingVectorWriter:
    """building embedding set과 같은 owner·revision의 번역 chunk에만 벡터를 쓴다."""

    def __init__(self, connection_pool):
        self._pool = connection_pool

    def put(self, scope: "EmbeddingWriteScope", chunk_id: str, embedding: Sequence[float]) -> None:
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError("remote_chunk_invalid")
        if not isinstance(scope, EmbeddingWriteScope):
            raise ValueError("remote_embedding_scope_invalid")
        dimension = scope.dimension
        vector = _vector_literal(embedding, dimension)
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO server_embedding_vectors
                       (owner_user_id, paper_id, server_version_id, parse_revision_id,
                        translation_revision_id, embedding_set_id, chunk_id, dimension, embedding)
                   SELECT e.owner_user_id, e.paper_id, e.server_version_id, e.parse_revision_id,
                          e.translation_revision_id, e.embedding_set_id, t.chunk_id, %s, %s::vector
                   FROM server_embedding_sets e
                   JOIN server_translation_chunks t
                     ON t.owner_user_id=e.owner_user_id
                    AND t.paper_id=e.paper_id
                    AND t.server_version_id=e.server_version_id
                    AND t.parse_revision_id=e.parse_revision_id
                    AND t.translation_revision_id=e.translation_revision_id
                   WHERE e.owner_user_id=%s AND e.paper_id=%s AND e.server_version_id=%s
                     AND e.parse_revision_id=%s AND e.translation_revision_id=%s
                     AND e.embedding_set_id=%s AND e.status='building'
                     AND t.chunk_id=%s
                     AND (e.model_fingerprint->>'dimension')::integer=%s
                   ON CONFLICT (owner_user_id, embedding_set_id, chunk_id) DO NOTHING
                   RETURNING chunk_id""",
                (
                    dimension,
                    vector,
                    scope.owner_user_id,
                    scope.paper_id,
                    scope.server_version_id,
                    scope.parse_revision_id,
                    scope.translation_revision_id,
                    scope.embedding_set_id,
                    chunk_id,
                    dimension,
                ),
            ).fetchone()
        if row is None:
            raise ValueError("remote_embedding_target_invalid")


@dataclass(frozen=True)
class EmbeddingWriteScope:
    """worker가 claim한 building set에서 만든다. 게시된 검색 binding과 섞지 않는다."""

    owner_user_id: str
    paper_id: str
    server_version_id: str
    parse_revision_id: str
    translation_revision_id: str
    embedding_set_id: str
    dimension: int

    def __post_init__(self):
        values = (
            self.owner_user_id,
            self.paper_id,
            self.server_version_id,
            self.parse_revision_id,
            self.translation_revision_id,
            self.embedding_set_id,
        )
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("remote_embedding_scope_invalid")
        if type(self.dimension) is not int or not 1 <= self.dimension <= 4096:
            raise ValueError("remote_embedding_scope_invalid")
