"""새 embedding set의 토큰 하위 조각을 현재 실행 세대에만 저장한다."""

import re

from .remote_vectors import EmbeddingWriteScope, _vector_literal


class PostgresEmbeddingFragmentWriter:
    def __init__(self, pool):
        self.pool = pool

    def put(self, scope, fragment, parent_token_count, embedding):
        from solo_leveling.infrastructure.embeddings.token_windows import TokenWindow
        if (type(scope) is not EmbeddingWriteScope or type(fragment) is not TokenWindow
                or fragment.embedding_set_id != scope.embedding_set_id
                or not re.fullmatch('[0-9a-f]{64}', fragment.embedding_fragment_id)
                or any(type(v) is not int for v in
                       (fragment.token_start, fragment.token_end, parent_token_count))
                or not 0 <= fragment.token_start < fragment.token_end <= parent_token_count
                or len(fragment.token_ids) != fragment.token_end - fragment.token_start):
            raise ValueError('remote_fragment_invalid')
        vector = _vector_literal(embedding, scope.dimension)
        with self.pool.connection() as conn:
            row = conn.execute('''INSERT INTO server_embedding_fragments
                (owner_user_id,paper_id,server_version_id,parse_revision_id,translation_revision_id,
                 embedding_set_id,chunk_id,embedding_fragment_id,token_start,token_end,
                 parent_token_count,dimension,embedding)
                SELECT e.owner_user_id,e.paper_id,e.server_version_id,e.parse_revision_id,
                       e.translation_revision_id,e.embedding_set_id,t.chunk_id,%s,%s,%s,%s,%s,%s::vector
                FROM server_embedding_sets e JOIN server_translation_chunks t
                  ON t.owner_user_id=e.owner_user_id AND t.paper_id=e.paper_id
                 AND t.server_version_id=e.server_version_id AND t.parse_revision_id=e.parse_revision_id
                 AND t.translation_revision_id=e.translation_revision_id
                WHERE e.owner_user_id=%s AND e.paper_id=%s AND e.server_version_id=%s
                  AND e.parse_revision_id=%s AND e.translation_revision_id=%s
                  AND e.embedding_set_id=%s AND e.status='building' AND t.chunk_id=%s
                  AND (e.model_fingerprint->>'dimension')::integer=%s
                  AND e.model_fingerprint->'split_policy'->>'version'='token-window-v1'
                ON CONFLICT (owner_user_id,embedding_set_id,embedding_fragment_id) DO UPDATE
                   SET embedding=server_embedding_fragments.embedding
                WHERE server_embedding_fragments.chunk_id=EXCLUDED.chunk_id
                  AND server_embedding_fragments.token_start=EXCLUDED.token_start
                  AND server_embedding_fragments.token_end=EXCLUDED.token_end
                  AND server_embedding_fragments.parent_token_count=EXCLUDED.parent_token_count
                  AND server_embedding_fragments.dimension=EXCLUDED.dimension
                  AND server_embedding_fragments.embedding=EXCLUDED.embedding
                RETURNING embedding_fragment_id''',
                (fragment.embedding_fragment_id,fragment.token_start,fragment.token_end,parent_token_count,
                 scope.dimension,vector,scope.owner_user_id,scope.paper_id,scope.server_version_id,
                 scope.parse_revision_id,scope.translation_revision_id,scope.embedding_set_id,
                 fragment.parent_chunk_id,scope.dimension)).fetchone()
        if row is None:
            raise ValueError('remote_fragment_target_invalid')
