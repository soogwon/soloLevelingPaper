"""원격 논문 삭제 요청을 tombstone과 작업 취소로 원자 접수한다."""

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class DeletionRequest:
    request_id: str
    paper_id: str
    status: str


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


def _view(row):
    return DeletionRequest(str(row["request_id"]), str(row["paper_id"]), row["status"])
