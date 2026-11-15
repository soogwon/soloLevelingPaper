"""tombstone 논문의 PostgreSQL 파생 데이터와 R2 원문을 멱등 정리한다."""

from dataclasses import dataclass

from solo_leveling.domain.deletion_policy import current_deletion_policy
from solo_leveling.interfaces.http.remote_deletions import (
    PostgresRemoteDeletionStore,
    RemoteDeletionCleanupService,
)


_DERIVED_DELETES = (
    """DELETE FROM server_provider_attempts a USING server_remote_executions e,
              server_execution_contexts c
       WHERE a.owner_user_id=%s AND e.owner_user_id=a.owner_user_id
         AND e.execution_id=a.execution_id AND c.owner_user_id=e.owner_user_id
         AND c.context_id=e.context_id AND c.paper_id=%s""",
    "DELETE FROM server_processing_evidences WHERE owner_user_id=%s AND paper_id=%s",
    """DELETE FROM server_remote_executions e USING server_execution_contexts c
       WHERE e.owner_user_id=%s AND c.owner_user_id=e.owner_user_id
         AND c.context_id=e.context_id AND c.paper_id=%s""",
    "DELETE FROM server_execution_contexts WHERE owner_user_id=%s AND paper_id=%s",
    "UPDATE server_processing_versions SET active_publication_id=NULL WHERE owner_user_id=%s AND paper_id=%s AND active_publication_id IS NOT NULL",
    "DELETE FROM server_processing_publications WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_embedding_vectors WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_embedding_sets WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_translation_chunks WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_translation_revisions WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_processing_chunks WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_parse_revisions WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_processing_jobs WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_resume_requests WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_summaries WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_annotations WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_learning_records WHERE owner_user_id=%s AND paper_id=%s",
    "DELETE FROM server_collection_papers WHERE owner_user_id=%s AND paper_id=%s",
    """DELETE FROM server_paper_reading_states s USING server_papers p
       WHERE p.owner_user_id=%s AND p.paper_id=%s AND s.paper_id=p.paper_id""",
)


@dataclass(frozen=True)
class DeletionBackendResult:
    deleted_objects: int = 0


class PostgresObjectDeletionBackend:
    """논문 부모와 tombstone을 남기고 고정 owner/paper의 자식만 삭제한다."""

    def __init__(self, pool, object_storage):
        self.pool = pool
        self.object_storage = object_storage

    def delete_derived(self, owner_user_id: str, paper_id: str) -> DeletionBackendResult:
        with self.pool.connection() as conn:
            self._lock_tombstone(conn, owner_user_id, paper_id)
            for statement in _DERIVED_DELETES:
                conn.execute(statement, (owner_user_id, paper_id))
        return DeletionBackendResult()

    def delete_source(self, owner_user_id: str, paper_id: str) -> DeletionBackendResult:
        with self.pool.connection() as conn:
            self._lock_tombstone(conn, owner_user_id, paper_id)
            rows = conn.execute(
                """SELECT object_key FROM server_pdf_uploads
                   WHERE owner_user_id=%s AND paper_id=%s ORDER BY object_key""",
                (owner_user_id, paper_id),
            ).fetchall()
        keys = tuple(row["object_key"] for row in rows)
        if any(not _safe_private_key(key) for key in keys):
            raise ValueError("remote_deletion_object_key_invalid")
        for key in keys:
            self.object_storage.delete_upload(key)
        with self.pool.connection() as conn:
            self._lock_tombstone(conn, owner_user_id, paper_id)
            conn.execute(
                "DELETE FROM server_remote_upload_intents WHERE owner_user_id=%s AND paper_id=%s",
                (owner_user_id, paper_id),
            )
            conn.execute(
                "DELETE FROM server_pdf_uploads WHERE owner_user_id=%s AND paper_id=%s",
                (owner_user_id, paper_id),
            )
            conn.execute(
                "DELETE FROM server_processing_versions WHERE owner_user_id=%s AND paper_id=%s",
                (owner_user_id, paper_id),
            )
            # 재생성 방지 식별자는 남기되 논문 표시 정보는 물리 정리한다.
            conn.execute(
                """UPDATE server_papers SET title=NULL,authors=NULL,publication_year=NULL,
                          updated_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s AND paper_id=%s""",
                (owner_user_id, paper_id),
            )
        return DeletionBackendResult(len(keys))

    @staticmethod
    def _lock_tombstone(conn, owner_user_id: str, paper_id: str) -> None:
        row = conn.execute(
            """SELECT paper_id FROM server_papers
               WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NOT NULL FOR UPDATE""",
            (owner_user_id, paper_id),
        ).fetchone()
        if row is None:
            raise ValueError("remote_deletion_tombstone_missing")


def create_remote_deletion_cleanup(
    pool,
    object_storage,
    *,
    worker_id: str,
    lease_seconds: int,
) -> RemoteDeletionCleanupService:
    """승인된 현재 정책과 실제 저장 backend를 삭제 worker에 조립한다."""
    return RemoteDeletionCleanupService(
        PostgresRemoteDeletionStore(pool),
        PostgresObjectDeletionBackend(pool, object_storage),
        current_deletion_policy(),
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )


def _safe_private_key(value) -> bool:
    return (isinstance(value, str) and value.startswith("private/")
            and ".." not in value.split("/") and "\\" not in value and "\x00" not in value)
