"""원격 처리 작업의 멱등 접수와 lease 기반 실행권을 관리한다."""

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class ProcessingJobConflict(Exception):
    """같은 요청 키가 다른 payload로 이미 사용되었다."""


class ProcessingJobNotFound(Exception):
    """인증 사용자 범위에서 작업을 찾지 못했다."""


class ProcessingJobLeaseLost(Exception):
    """worker가 더 이상 현재 실행 세대를 소유하지 않는다."""


@dataclass(frozen=True)
class ProcessingJob:
    job_id: str
    owner_user_id: str
    paper_id: str
    server_version_id: str
    job_kind: str
    request_key: str
    payload_hash: str
    status: JobStatus
    attempt: int
    max_attempts: int
    retryable: bool
    lease_token: str | None = None


class PostgresProcessingJobStore:
    def __init__(self, connection_pool):
        self._pool = connection_pool

    def enqueue(
        self,
        owner_user_id: str,
        paper_id: str,
        server_version_id: str,
        job_kind: str,
        request_key: str,
        payload_hash: str,
        *,
        max_attempts: int = 3,
        retryable: bool = True,
    ) -> ProcessingJob:
        if max_attempts < 1:
            raise ValueError("max_attempts_invalid")
        job_id = str(uuid4())
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO server_processing_jobs
                       (job_id, owner_user_id, paper_id, server_version_id, job_kind,
                        request_key, payload_hash, status, max_attempts, retryable)
                   SELECT %s, v.owner_user_id, v.paper_id, v.server_version_id, %s,
                          %s, %s, 'queued', %s, %s
                   FROM server_processing_versions v
                   WHERE v.owner_user_id=%s AND v.paper_id=%s AND v.server_version_id=%s
                   ON CONFLICT (owner_user_id, request_key) DO NOTHING
                   RETURNING *""",
                (
                    job_id,
                    job_kind,
                    request_key,
                    payload_hash,
                    max_attempts,
                    retryable,
                    owner_user_id,
                    paper_id,
                    server_version_id,
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT * FROM server_processing_jobs
                       WHERE owner_user_id=%s AND request_key=%s FOR UPDATE""",
                    (owner_user_id, request_key),
                ).fetchone()
                if row is None:
                    raise ProcessingJobNotFound("processing_version_not_found")
                if row["payload_hash"] != payload_hash:
                    raise ProcessingJobConflict("request_key_payload_conflict")
        return _job(row)

    def claim(self, worker_id: str, *, lease_seconds: int = 60) -> ProcessingJob | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds_invalid")
        lease_token = str(uuid4())
        # lease 판정은 모두 DB 시계로 한다. API·worker 서버의 시계가 어긋나도 판정이 갈리지 않는다.
        with self._pool.connection() as conn:
            row = conn.execute(
                """WITH abandoned AS (
                       UPDATE server_processing_jobs
                       SET status='failed', retryable=false,
                           error_code=CASE WHEN attempt >= max_attempts
                                           THEN 'max_attempts_exhausted'
                                           ELSE 'lease_expired_not_retryable' END,
                           lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                           heartbeat_at=NULL, finished_at=CURRENT_TIMESTAMP,
                           updated_at=CURRENT_TIMESTAMP
                       WHERE status='running' AND lease_expires_at <= CURRENT_TIMESTAMP
                         AND (attempt >= max_attempts OR retryable=false)
                       RETURNING job_id
                   ), candidate AS (
                       SELECT owner_user_id, job_id
                       FROM server_processing_jobs
                       WHERE (
                           status='queued'
                           OR (status='running' AND lease_expires_at <= CURRENT_TIMESTAMP
                               AND retryable=true)
                       )
                         AND attempt < max_attempts
                       ORDER BY created_at, job_id
                       LIMIT 1 FOR UPDATE SKIP LOCKED
                   )
                   UPDATE server_processing_jobs j
                   SET status='running', attempt=j.attempt+1, lease_owner=%s,
                       lease_token=%s,
                       lease_expires_at=CURRENT_TIMESTAMP + make_interval(secs => %s),
                       heartbeat_at=CURRENT_TIMESTAMP,
                       error_code=NULL, updated_at=CURRENT_TIMESTAMP, finished_at=NULL
                   FROM candidate c
                   WHERE j.owner_user_id=c.owner_user_id AND j.job_id=c.job_id
                   RETURNING j.*""",
                (worker_id, lease_token, lease_seconds),
            ).fetchone()
        return None if row is None else _job(row)

    def heartbeat(
        self,
        owner_user_id: str,
        job_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 60,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_processing_jobs
                   SET heartbeat_at=CURRENT_TIMESTAMP,
                       lease_expires_at=CURRENT_TIMESTAMP + make_interval(secs => %s),
                       updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND job_id=%s AND status='running'
                     AND lease_token=%s AND lease_expires_at>CURRENT_TIMESTAMP
                   RETURNING job_id""",
                (lease_seconds, owner_user_id, job_id, lease_token),
            ).fetchone()
        if row is None:
            raise ProcessingJobLeaseLost("processing_job_lease_lost")

    def succeed(self, owner_user_id: str, job_id: str, lease_token: str) -> None:
        self._finish(owner_user_id, job_id, lease_token, status="succeeded", error_code=None)

    def fail(
        self,
        owner_user_id: str,
        job_id: str,
        lease_token: str,
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_processing_jobs
                   SET status=CASE WHEN %s AND attempt < max_attempts THEN 'queued' ELSE 'failed' END,
                       retryable=%s, error_code=%s,
                       lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                       finished_at=CASE WHEN %s AND attempt < max_attempts THEN NULL
                                        ELSE CURRENT_TIMESTAMP END,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND job_id=%s AND status='running'
                     AND lease_token=%s AND lease_expires_at>CURRENT_TIMESTAMP
                   RETURNING job_id""",
                (
                    retryable,
                    retryable,
                    error_code,
                    retryable,
                    owner_user_id,
                    job_id,
                    lease_token,
                ),
            ).fetchone()
        if row is None:
            raise ProcessingJobLeaseLost("processing_job_lease_lost")

    def cancel(self, owner_user_id: str, job_id: str) -> None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_processing_jobs
                   SET status='cancelled', retryable=false, lease_owner=NULL, lease_token=NULL,
                       lease_expires_at=NULL, heartbeat_at=NULL,
                       finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND job_id=%s AND status IN ('queued', 'running')
                   RETURNING job_id""",
                (owner_user_id, job_id),
            ).fetchone()
        if row is None:
            raise ProcessingJobNotFound("processing_job_not_found")

    def _finish(
        self,
        owner_user_id: str,
        job_id: str,
        lease_token: str,
        *,
        status: Literal["succeeded"],
        error_code: str | None,
    ) -> None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_processing_jobs
                   SET status=%s, error_code=%s, lease_owner=NULL, lease_token=NULL,
                       lease_expires_at=NULL, heartbeat_at=NULL,
                       finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND job_id=%s AND status='running'
                     AND lease_token=%s AND lease_expires_at>CURRENT_TIMESTAMP
                   RETURNING job_id""",
                (status, error_code, owner_user_id, job_id, lease_token),
            ).fetchone()
        if row is None:
            raise ProcessingJobLeaseLost("processing_job_lease_lost")


def _job(row) -> ProcessingJob:
    return ProcessingJob(
        str(row["job_id"]),
        row["owner_user_id"],
        str(row["paper_id"]),
        row["server_version_id"],
        row["job_kind"],
        row["request_key"],
        row["payload_hash"],
        row["status"],
        int(row["attempt"]),
        int(row["max_attempts"]),
        bool(row["retryable"]),
        None if row.get("lease_token") is None else str(row["lease_token"]),
    )
