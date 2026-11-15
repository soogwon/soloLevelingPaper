"""원격 논문 삭제 요청을 tombstone과 작업 취소로 원자 접수한다."""

from dataclasses import dataclass
from uuid import uuid4

from solo_leveling.domain.deletion_policy import DeletionPolicy


@dataclass(frozen=True)
class DeletionRequest:
    request_id: str
    paper_id: str
    status: str


@dataclass(frozen=True)
class DeletionCleanup:
    request_id: str
    owner_user_id: str
    paper_id: str
    attempt_count: int


class PostgresRemoteDeletionStore:
    def __init__(self, pool):
        self._pool = pool

    def request(self, owner_user_id: str, paper_id: str, request_key: str) -> DeletionRequest | None:
        if not all(isinstance(v, str) and v.strip() for v in (owner_user_id, paper_id, request_key)):
            raise ValueError("remote_deletion_input_invalid")
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (owner_user_id,))
            old = conn.execute(
                """SELECT request_id,paper_id,status FROM server_deletion_requests
                   WHERE owner_user_id=%s AND (request_key=%s OR paper_id=%s)
                   ORDER BY (request_key=%s) DESC LIMIT 1 FOR UPDATE""",
                (owner_user_id, request_key, paper_id, request_key),
            ).fetchone()
            if old:
                if str(old["paper_id"]) != paper_id:
                    raise ValueError("remote_deletion_request_conflict")
                return _view(old)
            paper = conn.execute(
                """SELECT paper_id FROM server_papers
                   WHERE owner_user_id=%s AND paper_id=%s FOR UPDATE""",
                (owner_user_id, paper_id),
            ).fetchone()
            if paper is None:
                return None
            conn.execute(
                """UPDATE server_processing_jobs SET status='cancelled',retryable=false,error_code='paper_tombstoned',
                          lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                          finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND paper_id=%s AND status IN ('queued','running')""",
                (owner_user_id, paper_id),
            )
            conn.execute(
                """UPDATE server_papers SET deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND paper_id=%s""",
                (owner_user_id, paper_id),
            )
            row = conn.execute(
                """INSERT INTO server_deletion_requests(request_id,owner_user_id,paper_id,request_key,status)
                   VALUES (%s,%s,%s,%s,'blocking') RETURNING request_id,paper_id,status""",
                (str(uuid4()), owner_user_id, paper_id, request_key),
            ).fetchone()
        return _view(row)

    def claim_cleanup(self) -> DeletionCleanup | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """WITH candidate AS (
                       SELECT request_id FROM server_deletion_requests
                       WHERE status IN ('blocking','failed')
                       ORDER BY updated_at,request_id LIMIT 1 FOR UPDATE SKIP LOCKED
                   )
                   UPDATE server_deletion_requests d
                   SET status='cleaning',attempt_count=attempt_count+1,last_error_code=NULL,
                       updated_at=CURRENT_TIMESTAMP
                   FROM candidate c WHERE d.request_id=c.request_id
                   RETURNING d.request_id,d.owner_user_id,d.paper_id,d.attempt_count"""
            ).fetchone()
        if row is None:
            return None
        return DeletionCleanup(str(row["request_id"]), row["owner_user_id"],
                               str(row["paper_id"]), int(row["attempt_count"]))

    def mark_cleanup_failed(self, request_id: str, error_code: str) -> None:
        if not error_code or len(error_code) > 80:
            raise ValueError("remote_deletion_error_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_deletion_requests
                   SET status='failed',last_error_code=%s,updated_at=CURRENT_TIMESTAMP
                   WHERE request_id=%s AND status='cleaning' RETURNING request_id""",
                (error_code, request_id),
            ).fetchone()
        if row is None:
            raise ValueError("remote_deletion_cleanup_stale")

    def mark_cleanup_complete(self, request_id: str) -> None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_deletion_requests
                   SET status='complete',last_error_code=NULL,completed_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE request_id=%s AND status='cleaning' RETURNING request_id""",
                (request_id,),
            ).fetchone()
        if row is None:
            raise ValueError("remote_deletion_cleanup_stale")


class RemoteDeletionCleanupService:
    """tombstone 이후 저장소별 물리 정리를 재시도 가능한 한 실행으로 묶는다."""

    def __init__(self, store, backend, policy: DeletionPolicy):
        self.store, self.backend, self.policy = store, backend, policy

    def run_once(self) -> dict[str, object]:
        self.policy.validate_for_physical_deletion()
        cleanup = self.store.claim_cleanup()
        if cleanup is None:
            return {"status": "idle"}
        try:
            self.backend.delete_derived(cleanup.owner_user_id, cleanup.paper_id)
            self.backend.delete_source(cleanup.owner_user_id, cleanup.paper_id)
        except Exception as exc:
            code = getattr(exc, "error_code", None) or "physical_cleanup_failed"
            self.store.mark_cleanup_failed(cleanup.request_id, code)
            return {"status": "failed", "attempt_count": cleanup.attempt_count,
                    "error_code": code}
        self.store.mark_cleanup_complete(cleanup.request_id)
        return {"status": "complete", "attempt_count": cleanup.attempt_count}


def _view(row):
    return DeletionRequest(str(row["request_id"]), str(row["paper_id"]), row["status"])
