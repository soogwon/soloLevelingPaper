"""owner 범위의 원격 실행 context를 게시된 revision 집합으로 해석한다."""

import json

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding


class PostgresRemoteContextResolver:
    """active pointer가 아니라 context가 고정한 과거 publication을 조회한다."""

    def __init__(self, connection_pool):
        self._pool = connection_pool

    def resolve(self, owner_user_id: str, context_id: str) -> RemoteSearchBinding | None:
        if not all(isinstance(value, str) and value.strip() for value in (owner_user_id, context_id)):
            raise ValueError("remote_context_lookup_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT c.owner_user_id, c.context_id, c.paper_id, c.server_version_id,
                          p.publication_id, p.parse_revision_id, p.translation_revision_id,
                          p.embedding_set_id, p.publication_policy, e.model_fingerprint
                   FROM server_execution_contexts c
                   JOIN server_processing_publications p
                     ON p.owner_user_id=c.owner_user_id
                    AND p.paper_id=c.paper_id
                    AND p.server_version_id=c.server_version_id
                    AND p.publication_id=c.publication_id
                    AND p.parse_revision_id=c.parse_revision_id
                   JOIN server_embedding_sets e
                     ON e.owner_user_id=p.owner_user_id
                    AND e.paper_id=p.paper_id
                    AND e.server_version_id=p.server_version_id
                    AND e.parse_revision_id=p.parse_revision_id
                    AND e.translation_revision_id=p.translation_revision_id
                    AND e.embedding_set_id=p.embedding_set_id
                   WHERE c.owner_user_id=%s AND c.context_id=%s
                     AND e.status='ready'""",
                (owner_user_id, context_id),
            ).fetchone()
        if row is None:
            return None
        fingerprint = row["model_fingerprint"]
        if not isinstance(fingerprint, dict) or not fingerprint:
            raise ValueError("remote_model_fingerprint_invalid")
        return RemoteSearchBinding(
            owner_user_id=str(row["owner_user_id"]),
            context_id=str(row["context_id"]),
            paper_id=str(row["paper_id"]),
            server_version_id=str(row["server_version_id"]),
            publication_id=str(row["publication_id"]),
            parse_revision_id=str(row["parse_revision_id"]),
            translation_revision_id=str(row["translation_revision_id"]),
            embedding_set_id=str(row["embedding_set_id"]),
            model_fingerprint=json.dumps(
                fingerprint,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            publication_policy=str(row["publication_policy"]),
        )
